"""Regression tests for Ogre's one-coin gamble, using temporary SQLite."""
import ast
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from cogs.roll_coins import (
    UK, ensure_coin_schema, coin_total, coin_standings,
    settle_completed_days, daily_coin_prize, day_award, award_missing_day,
)
from cogs.roll_gamble import gamble_once, GambleError


SOURCE = (Path(__file__).resolve().parents[1] / "rollf.py").read_text()


def load_roll_writer(connection):
    tree = ast.parse(SOURCE)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "insert_roll"
    )
    namespace = dict(
        sqlite3=sqlite3, time=time, datetime=datetime, timedelta=timedelta,
        TZ=UK, db=lambda: connection,
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "rollf.py", "exec"),
         namespace)
    return namespace["insert_roll"]


def setup_schema(con):
    con.executescript("""
        CREATE TABLE rolls (
            user_id INTEGER NOT NULL, username TEXT NOT NULL,
            value INTEGER NOT NULL, rolled_at INTEGER NOT NULL,
            actor_type TEXT NOT NULL, roll_date TEXT
        );
        CREATE UNIQUE INDEX idx_one_roll_per_day
        ON rolls(user_id, roll_date) WHERE actor_type='user';
        CREATE UNIQUE INDEX idx_one_gamble_roll_per_day
        ON rolls(user_id, roll_date) WHERE actor_type='gamble';
        CREATE TABLE user_scores (
            user_id INTEGER PRIMARY KEY,
            score INTEGER NOT NULL DEFAULT 0,
            rolls INTEGER NOT NULL DEFAULT 0,
            best INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE daily_scores (
            user_id INTEGER, roll_date TEXT, score INTEGER NOT NULL,
            rolls INTEGER NOT NULL,
            PRIMARY KEY(user_id, roll_date)
        );
    """)
    ensure_coin_schema(con)


NOW = datetime(2026, 11, 1, 11, 30, tzinfo=UK)


class GambleDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        setup_schema(self.con)
        self.writer = load_roll_writer(self.con)

    def tearDown(self):
        self.con.close()

    def award(self, user_id=101, day="2026-10-30", coins=1):
        self.con.execute("""
            INSERT INTO roll_coin_awards
                (guild_id, roll_date, user_id, username, winning_roll,
                 source, awarded_at, coins_awarded)
            VALUES (9, ?, ?, 'Gamer', 99, 'manual', 123456, ?)
        """, (day, user_id, coins))

    def gamble(self, *, user_id=101, first=25, extra=85, now=NOW):
        return gamble_once(
            self.con, 9, user_id, "Gamer", first_value=first,
            extra_value=extra, roll_writer=self.writer, now=now,
        )

    def test_without_coin_no_roll_or_charge(self):
        with self.assertRaisesRegex(GambleError, "shiny coin"):
            self.gamble()
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM rolls").fetchone()[0], 0)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM roll_coin_gambles").fetchone()[0], 0)

    def test_no_free_roll_performs_two_rolls_and_returns_stake_if_higher(self):
        self.award()
        result = self.gamble(first=20, extra=88)
        self.assertEqual(result["best"], 88)
        self.assertFalse(result["had_free_roll"])
        self.assertTrue(result["stake_returned"])
        self.assertEqual(result["balance"], 1)
        self.assertEqual(self.con.execute("""
            SELECT actor_type, value FROM rolls ORDER BY rowid
        """).fetchall(), [("user", 20), ("gamble", 88)])
        self.assertEqual(self.con.execute(
            "SELECT score,rolls,best FROM user_scores WHERE user_id=101"
        ).fetchone(), (108, 2, 88))
        self.assertEqual(self.con.execute(
            "SELECT score,rolls FROM daily_scores WHERE user_id=101"
        ).fetchone(), (108, 2))
        self.assertEqual(coin_total(self.con, 9, 101), 1)

    def test_existing_free_roll_only_gets_one_extra_roll(self):
        self.award()
        self.assertTrue(self.writer(
            101, "Gamer", 33, "user", timestamp=int(NOW.timestamp())))
        outcome = self.gamble(first=99, extra=90)
        self.assertTrue(outcome["had_free_roll"])
        self.assertEqual(outcome["base"], 33)
        self.assertEqual(outcome["best"], 90)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM rolls").fetchone()[0], 2)
        self.assertFalse(self.writer(
            101, "Gamer", 100, "user", timestamp=int(NOW.timestamp())))

    def test_lost_bet_debits_exactly_one_coin_and_never_changes_award(self):
        self.award()
        original_awards = self.con.execute(
            "SELECT * FROM roll_coin_awards").fetchall()
        outcome = self.gamble(first=94, extra=20)
        self.assertEqual(outcome["best"], 94)
        self.assertFalse(outcome["stake_returned"])
        self.assertEqual(outcome["balance"], 0)
        self.assertEqual(coin_total(self.con, 9, 101), 0)
        self.assertEqual(coin_standings(self.con, 9), [])
        self.assertEqual(
            self.con.execute("SELECT * FROM roll_coin_awards").fetchall(),
            original_awards)
        with self.assertRaisesRegex(GambleError, "one gamble per day"):
            self.gamble(first=1, extra=100)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM rolls").fetchone()[0], 2)

    def test_tie_loses_stake_and_cannot_roll_more_than_twice(self):
        self.award()
        result = self.gamble(first=55, extra=55)
        self.assertFalse(result["stake_returned"])
        self.assertEqual(result["balance"], 0)
        self.assertEqual(
            self.con.execute("SELECT stake_returned FROM roll_coin_gambles").fetchone()[0], 0)
        with self.assertRaises(GambleError):
            self.gamble(first=2, extra=100)

    def test_failure_after_rolls_rolls_back_all_scores_and_coin_charge(self):
        self.award()
        self.con.execute("""
            CREATE TRIGGER block_gamble
            BEFORE INSERT ON roll_coin_gambles
            BEGIN SELECT RAISE(ABORT, 'test transaction abort'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.gamble(first=44, extra=77)
        self.assertEqual(coin_total(self.con, 9, 101), 1)
        for table in ("rolls", "user_scores", "daily_scores", "roll_coin_gambles"):
            with self.subTest(table=table):
                self.assertEqual(
                    self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_sunday_double_coin_award_is_limited_to_daily_winner(self):
        self.con.execute("""
            INSERT INTO rolls VALUES
                (101,'Gamer',80,?,'user','2026-11-01'),
                (101,'Gamer',99,?,'gamble','2026-11-01'),
                (102,'Other',90,?,'user','2026-11-01')
        """, (int(NOW.timestamp()), int(NOW.timestamp()), int(NOW.timestamp())))
        count = settle_completed_days(
            self.con, 9, datetime(2026, 11, 2, 0, 2, tzinfo=UK))
        self.assertEqual(count, 1)
        self.assertEqual(day_award(self.con, 9, "2026-11-01")["user_id"], 101)
        self.assertEqual(day_award(self.con, 9, "2026-11-01")["winning_roll"], 99)
        self.assertEqual(day_award(self.con, 9, "2026-11-01")["coins_awarded"], 2)
        self.assertEqual(coin_total(self.con, 9, 101), 2)
        self.assertEqual(coin_total(self.con, 9, 102), 0)
        self.assertEqual(settle_completed_days(
            self.con, 9, datetime(2026, 11, 2, 8, 0, tzinfo=UK)), 0)

    def test_old_sunday_remains_one_coin_and_migration_is_idempotent(self):
        self.award(day="2026-10-25")
        before = self.con.execute(
            "SELECT coins_awarded FROM roll_coin_awards").fetchone()[0]
        ensure_coin_schema(self.con)
        self.assertEqual(before, 1)
        self.assertEqual(daily_coin_prize("2026-10-25"), 1)
        self.assertEqual(daily_coin_prize("2026-11-01"), 2)
        self.assertEqual(daily_coin_prize("2026-11-02"), 1)
        self.assertEqual(coin_total(self.con, 9, 101), 1)

    def test_manual_recovery_of_future_sunday_is_two_coins(self):
        self.assertTrue(award_missing_day(
            self.con, 9, "2026-11-01", 101, "Gamer", 100, 1,
            datetime(2026, 11, 2, 12, 0, tzinfo=UK)))
        self.assertEqual(day_award(self.con, 9, "2026-11-01")["coins_awarded"], 2)


class ConcurrentGambleTests(unittest.TestCase):
    def test_two_sessions_cannot_spend_same_coin_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "coins.db")
            with closing(sqlite3.connect(path, isolation_level=None)) as con:
                setup_schema(con)
                con.execute("""
                    INSERT INTO roll_coin_awards
                        (guild_id,roll_date,user_id,username,winning_roll,
                         source,awarded_at,coins_awarded)
                    VALUES (9,'2026-10-30',101,'Gamer',99,'manual',123456,1)
                """)

            def attempt(extra):
                with closing(sqlite3.connect(
                    path, isolation_level=None, timeout=8,
                )) as con:
                    try:
                        return gamble_once(
                            con, 9, 101, "Gamer",
                            first_value=25, extra_value=extra,
                            roll_writer=load_roll_writer(con), now=NOW)
                    except GambleError:
                        return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, (12, 90)))
            self.assertEqual(sum(result is not None for result in results), 1)
            with closing(sqlite3.connect(path)) as con:
                self.assertEqual(
                    con.execute("SELECT COUNT(*) FROM roll_coin_gambles").fetchone()[0], 1)
                self.assertEqual(
                    con.execute("SELECT COUNT(*) FROM rolls").fetchone()[0], 2)
                self.assertGreaterEqual(coin_total(con, 9, 101), 0)


if __name__ == "__main__":
    unittest.main()
