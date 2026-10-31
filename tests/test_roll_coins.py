from contextlib import closing
"""Coin accounting uses temporary SQLite only; never the live bot database."""
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cogs.roll_coins import (
    UK, ensure_coin_schema, settle_completed_days, coin_total,
    coin_standings, day_award, award_missing_day,
    claim_public_coin_leaderboard, release_public_coin_leaderboard,
)


def clock(value):
    return datetime.fromisoformat(value).replace(tzinfo=UK)


class CoinAccountingTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:', isolation_level=None)
        self.con.execute('CREATE TABLE rolls (user_id, username, value, rolled_at, actor_type, roll_date)')
        ensure_coin_schema(self.con)
        self.now = clock('2026-10-26T00:00:00')

    def tearDown(self):
        self.con.close()

    def roll(self, uid, score, when, actor='user'):
        self.con.execute('INSERT INTO rolls VALUES (?, ?, ?, ?, ?, ?)',
                         (uid, str(uid), score, int(clock(when).timestamp()), actor, 'wrong-legacy-date'))

    def test_ledger_only_one_public_display_per_guild_uk_day(self):
        self.assertTrue(claim_public_coin_leaderboard(
            self.con, 9, "2026-10-31", 101, self.now))
        self.assertFalse(claim_public_coin_leaderboard(
            self.con, 9, "2026-10-31", 102, self.now))
        self.assertEqual(self.con.execute(
            "SELECT requested_by FROM roll_public_leaderboards "
            "WHERE guild_id=9 AND roll_date='2026-10-31'"
        ).fetchone()[0], 101)
        # Both a new day and a different guild have an independent allowance.
        self.assertTrue(claim_public_coin_leaderboard(
            self.con, 9, "2026-11-01", 102, self.now))
        self.assertTrue(claim_public_coin_leaderboard(
            self.con, 10, "2026-10-31", 202, self.now))
        ensure_coin_schema(self.con)  # Restart-style schema reinitialisation.
        self.assertFalse(claim_public_coin_leaderboard(
            self.con, 9, "2026-10-31", 103, self.now))
        self.assertEqual(coin_standings(self.con, 9), [])

    def test_public_ledger_reservation_survives_reconnect_and_can_release_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.db"
            with closing(sqlite3.connect(path, isolation_level=None)) as con:
                ensure_coin_schema(con)
                self.assertTrue(claim_public_coin_leaderboard(
                    con, 9, "2026-10-31", 101, self.now))
            with closing(sqlite3.connect(path, isolation_level=None)) as con:
                ensure_coin_schema(con)
                self.assertFalse(claim_public_coin_leaderboard(
                    con, 9, "2026-10-31", 102, self.now))
                self.assertFalse(release_public_coin_leaderboard(
                    con, 9, "2026-10-31", 102))
                self.assertTrue(release_public_coin_leaderboard(
                    con, 9, "2026-10-31", 101))
                self.assertTrue(claim_public_coin_leaderboard(
                    con, 9, "2026-10-31", 102, self.now))

    def test_backfill_and_repeated_restart_are_idempotent(self):
        self.roll(1, 46, '2026-10-24T01:00:00')
        self.roll(2, 37, '2026-10-24T02:00:00')
        self.roll(2, 92, '2026-10-25T03:00:00')
        before = self.con.execute('SELECT * FROM rolls').fetchall()
        self.assertEqual(settle_completed_days(self.con, 9, self.now), 2)
        ensure_coin_schema(self.con)
        self.assertEqual(settle_completed_days(self.con, 9, self.now), 0)
        self.assertEqual(coin_total(self.con, 9, 1), 1)
        self.assertEqual(coin_total(self.con, 9, 2), 1)
        self.assertEqual(self.con.execute('SELECT * FROM rolls').fetchall(), before)

    def test_first_highest_wins_ties_including_same_second(self):
        self.roll(20, 99, '2026-10-25T05:00:00')
        self.roll(10, 99, '2026-10-25T05:00:00')
        self.roll(3, 99, '2026-10-25T06:00:00')
        settle_completed_days(self.con, 9, self.now)
        self.assertEqual(day_award(self.con, 9, '2026-10-25')['user_id'], 20)

    def test_ogre_can_win_coin_and_appear_on_leaderboard(self):
        self.roll(0, 100, '2026-10-25T01:00:00', 'bot')
        self.roll(1, 90, '2026-10-25T23:59:59')
        self.roll(2, 100, '2026-10-26T00:00:00')
        self.assertEqual(settle_completed_days(self.con, 9, self.now), 1)
        self.assertEqual(coin_total(self.con, 9, 0), 1)
        self.assertEqual(coin_total(self.con, 9, 2), 0)
        self.assertEqual(day_award(self.con, 9, '2026-10-25')['user_id'], 0)
        self.assertEqual(coin_standings(self.con, 9)[0]['user_id'], 0)

    def test_ogre_and_user_tie_awards_earliest_roll(self):
        self.roll(1, 99, '2026-10-25T01:00:00')
        self.roll(0, 99, '2026-10-25T02:00:00', 'bot')
        self.assertEqual(settle_completed_days(self.con, 9, self.now), 1)
        self.assertEqual(day_award(self.con, 9, '2026-10-25')['user_id'], 1)

    def test_ogre_can_win_tie_if_he_rolls_first(self):
        self.roll(0, 99, '2026-10-25T01:00:00', 'bot')
        self.roll(1, 99, '2026-10-25T02:00:00')
        self.assertEqual(settle_completed_days(self.con, 9, self.now), 1)
        self.assertEqual(day_award(self.con, 9, '2026-10-25')['user_id'], 0)

    def test_summer_midnight_uses_uk_not_legacy_utc_roll_date(self):
        self.roll(1, 80, '2026-07-02T00:05:00')
        self.assertEqual(settle_completed_days(self.con, 9, clock('2026-07-02T12:00:00')), 0)
        settle_completed_days(self.con, 9, clock('2026-07-03T00:00:00'))
        self.assertIsNotNone(day_award(self.con, 9, '2026-07-02'))
        self.assertIsNone(day_award(self.con, 9, '2026-07-01'))

    def test_both_autumn_repeated_hours_belong_to_same_day(self):
        for uid, stamp in ((1, '2026-10-25T01:30:00+01:00'),
                           (2, '2026-10-25T01:30:00+00:00')):
            self.con.execute('INSERT INTO rolls VALUES (?, ?, 90, ?, ?, ?)',
                             (uid, str(uid), int(datetime.fromisoformat(stamp).timestamp()), 'user', 'bad'))
        self.assertEqual(settle_completed_days(self.con, 9, self.now), 1)
        self.assertEqual(day_award(self.con, 9, '2026-10-25')['user_id'], 1)

    def test_no_rolls_means_no_coin(self):
        self.assertEqual(settle_completed_days(self.con, 9, self.now), 0)
        self.assertEqual(coin_standings(self.con, 9), [])

    def test_manual_missing_day_is_one_coin_and_never_overwrites(self):
        self.assertTrue(award_missing_day(self.con, 9, '2026-10-24', 1, 'one', 46, 99, self.now))
        self.assertFalse(award_missing_day(self.con, 9, '2026-10-24', 2, 'two', 90, 99, self.now))
        self.roll(2, 99, '2026-10-24T01:00:00')
        settle_completed_days(self.con, 9, self.now)
        self.assertEqual(coin_total(self.con, 9, 1), 1)
        self.assertEqual(coin_total(self.con, 9, 2), 0)

    def test_saved_history_takes_priority_over_manual_award(self):
        self.roll(2, 92, '2026-10-25T10:00:00')
        self.assertFalse(award_missing_day(self.con, 9, '2026-10-25', 1, 'one', 46, 99, self.now))
        self.assertEqual(coin_total(self.con, 9, 2), 1)

    def test_invalid_and_unfinished_manual_dates_are_rejected(self):
        for day, value in [('bad', 50), ('20261024', 50), ('2026-10-26', 50), ('2026-10-27', 50),
                           ('2026-10-24', 0), ('2026-10-24', 101)]:
            with self.subTest(day=day, value=value), self.assertRaises(ValueError):
                award_missing_day(self.con, 9, day, 1, 'one', value, 99, self.now)
        self.assertEqual(coin_standings(self.con, 9), [])

    def test_standings_have_shared_ranks_and_guild_scope(self):
        for uid, day in [(1, '2026-10-20'), (1, '2026-10-21'), (2, '2026-10-22'),
                         (2, '2026-10-23'), (3, '2026-10-24')]:
            award_missing_day(self.con, 9, day, uid, str(uid), 50, 99, self.now)
        rows = coin_standings(self.con, 9)
        self.assertEqual([(r['rank'], r['coins']) for r in rows], [(1, 2), (1, 2), (3, 1)])
        self.assertEqual(coin_standings(self.con, 10), [])

    def test_restart_with_separate_connection_preserves_totals(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'coins.db'
            with closing(sqlite3.connect(path, isolation_level=None)) as con:
                con.execute('CREATE TABLE rolls (user_id, username, value, rolled_at, actor_type)')
                con.execute("INSERT INTO rolls VALUES (1, 'one', 100, ?, 'user')",
                            (int(clock('2026-10-25T05:00:00').timestamp()),))
                ensure_coin_schema(con)
                settle_completed_days(con, 9, self.now)
            with closing(sqlite3.connect(path, isolation_level=None)) as con:
                ensure_coin_schema(con)
                self.assertEqual(settle_completed_days(con, 9, self.now), 0)
                self.assertEqual(coin_total(con, 9, 1), 1)
