"""Discord-level safety checks for Ogre Gamble and Sunday bonus chat."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import json
import sqlite3
import unittest

import discord

from cogs.roll_coins import UK, ensure_coin_schema, coin_total, daily_coin_prize
from cogs.roll_gamble import gamble_once, GambleError
from cogs.roll_panels import DailyRollPanel

SOURCE = (Path(__file__).resolve().parents[1] / "rollf.py").read_text()


def load(names, namespace):
    tree = ast.parse(SOURCE)
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in names
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "rollf.py", "exec"),
         namespace)


class FixedClock(datetime):
    current = datetime(2026, 11, 1, 10, 5, tzinfo=UK)

    @classmethod
    def now(cls, tz=None):
        return cls.current


class FakeSundayChannel:
    def __init__(self):
        self.messages = []
        self.send = AsyncMock(side_effect=self.post)

    async def post(self, content, **kwargs):
        message = SimpleNamespace(
            id=100 + len(self.messages), author=SimpleNamespace(id=999),
            content=content, created_at=FixedClock.current,
        )
        self.messages.append(message)
        return message

    async def history(self, **kwargs):
        for message in self.messages:
            yield message


class GambleUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.executescript("""
            CREATE TABLE rolls (
                user_id INTEGER, username TEXT, value INTEGER, rolled_at INTEGER,
                actor_type TEXT, roll_date TEXT
            );
            CREATE UNIQUE INDEX idx_one_roll_per_day
                ON rolls(user_id,roll_date) WHERE actor_type='user';
            CREATE UNIQUE INDEX idx_one_gamble_roll_per_day
                ON rolls(user_id,roll_date) WHERE actor_type='gamble';
            CREATE TABLE user_scores (
                user_id INTEGER PRIMARY KEY,
                score INTEGER NOT NULL DEFAULT 0, rolls INTEGER NOT NULL DEFAULT 0,
                best INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE daily_scores (
                user_id INTEGER, roll_date TEXT, score INTEGER NOT NULL,
                rolls INTEGER NOT NULL, PRIMARY KEY(user_id,roll_date)
            );
        """)
        ensure_coin_schema(self.con)
        self.channel = SimpleNamespace(send=AsyncMock(
            return_value=SimpleNamespace(id=200, reply=AsyncMock())))
        rolls = iter([91, 77, 55])
        self.ns = dict(
            discord=discord, HYPED_GUILD_ID=9, ROLL_CHANNEL_ID=88,
            TZ=UK, datetime=FixedClock, timedelta=timedelta,
            db=lambda: self.con, coin_total=coin_total,
            today_range=lambda: (
                int(datetime(2026, 11, 1, 0, 0, tzinfo=UK).timestamp()),
                int(datetime(2026, 11, 2, 0, 0, tzinfo=UK).timestamp()),
            ),
            daily_coin_prize=daily_coin_prize,
            ogre_coin_emoji=lambda: "<:ogrecoin:789>",
            secrets=SimpleNamespace(randbelow=lambda n: next(rolls) - 1),
            gamble_once=gamble_once, GambleError=GambleError,
            log_error=lambda message: None, log_event=lambda message: None,
            sqlite3=sqlite3,
            reply_if_tied_for_highest=AsyncMock(),
            roll_reactions=SimpleNamespace(sync=AsyncMock()),
            sync_high_roller_role=AsyncMock(),
        )
        # These functions are compiled without importing bot startup/config.
        load({"insert_roll", "GambleConfirmView", "show_gamble_rules"}, self.ns)
        self.ns["insert_roll"].__globals__.update(
            time=SimpleNamespace(time=lambda: int(FixedClock.current.timestamp()))
        )

    def tearDown(self):
        self.con.close()

    def award(self, user_id=101):
        self.con.execute("""
            INSERT INTO roll_coin_awards
                (guild_id,roll_date,user_id,username,winning_roll,source,awarded_at)
            VALUES (9,'2026-10-30',?,'Gamer',90,'manual',12345)
        """, (user_id,))

    def free_roll(self, value=19, user_id=101):
        self.assertTrue(self.ns["insert_roll"](
            user_id, "Gamer", value, "user",
            timestamp=int(FixedClock.current.timestamp()),
        ))

    def interaction(self, user_id=101):
        return SimpleNamespace(
            guild_id=9, channel_id=88,
            user=SimpleNamespace(id=user_id, name="Gamer", mention=f"<@{user_id}>"),
            channel=self.channel,
            response=SimpleNamespace(
                send_message=AsyncMock(), defer=AsyncMock(),
                edit_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_red_gamble_button_is_added_to_daily_panel(self):
        callbacks = [AsyncMock() for _ in range(4)]
        panel = DailyRollPanel(
            day="2026-11-01", winner_text="", tagline="Ogre keep score",
            on_roll=callbacks[0], on_gamble=callbacks[1],
            on_leaderboard=callbacks[2], on_stats=callbacks[3],
        )
        buttons = [
            item for item in panel.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual([button.label for button in buttons],
                         ["Roll", "Gamble", "Leaderboard", "My stats"])
        self.assertEqual(buttons[1].style, discord.ButtonStyle.danger)
        self.assertEqual(str(buttons[0].emoji), "<:ogredice:1566126699043880992>")
        self.assertEqual(str(buttons[1].emoji), "<:ogrecoin:1565481626488807434>")
        self.assertEqual(buttons[1].custom_id, "hyped_ogre_coin_gamble_v1")
        self.assertTrue(panel.is_persistent())

    async def test_no_coin_gives_private_ogre_warning(self):
        i = self.interaction()
        await self.ns["show_gamble_rules"](i)
        i.response.send_message.assert_awaited_once()
        args = i.response.send_message.await_args
        self.assertIn("Ogre need one shiny coin", args.args[0])
        self.assertTrue(args.kwargs["ephemeral"])
        self.assertFalse(args.kwargs.get("view"))
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM rolls").fetchone()[0], 0)

    async def test_roll_first_gives_private_prompt_without_gamble_confirmation(self):
        self.award()
        i = self.interaction()
        await self.ns["show_gamble_rules"](i)
        response = i.response.send_message.await_args
        self.assertIn("roll first", response.args[0])
        self.assertIn("decide", response.args[0])
        self.assertTrue(response.kwargs["ephemeral"])
        self.assertIsNone(response.kwargs.get("view"))
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM rolls").fetchone()[0], 0)
        self.assertEqual(coin_total(self.con, 9, 101), 1)

    async def test_explanation_private_then_confirm_posts_one_public_gamble(self):
        self.award()
        self.free_roll(19)
        i = self.interaction()
        await self.ns["show_gamble_rules"](i)
        kwargs = i.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("Higher second roll", i.response.send_message.await_args.args[0])
        self.assertIn("Your first roll: **19**", i.response.send_message.await_args.args[0])
        self.assertNotIn("back to back", i.response.send_message.await_args.args[0])
        self.assertIn("once per UK day", i.response.send_message.await_args.args[0])
        view = kwargs["view"]
        self.assertIsInstance(view, self.ns["GambleConfirmView"])
        self.assertFalse(await view.interaction_check(self.interaction(999)))
        self.assertTrue(await view.interaction_check(i))
        gamble_button = next(
            child for child in view.children
            if isinstance(child, discord.ui.Button) and child.label == "Gamble 1 coin")
        await gamble_button.callback(i)
        self.assertEqual(
            [r[0] for r in self.con.execute(
                "SELECT actor_type FROM rolls ORDER BY rowid").fetchall()],
            ["user", "gamble"])
        self.assertEqual(coin_total(self.con, 9, 101), 1)
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertIn("19", self.channel.send.await_args.args[0])
        self.assertIn("91", self.channel.send.await_args.args[0])
        self.assertIn("<:ogredice:1566126699043880992>", self.channel.send.await_args.args[0])
        self.assertIn("Gamer risked a coin to roll again:", self.channel.send.await_args.args[0])
        self.assertIn("Ogre say good roll. Have coin back!", self.channel.send.await_args.args[0])
        self.ns["roll_reactions"].sync.assert_awaited_once()
        self.ns["sync_high_roller_role"].assert_awaited_once()
        i.followup.send.assert_awaited_once()
        self.assertTrue(i.followup.send.await_args.kwargs["ephemeral"])

    async def test_one_gamble_daily_then_new_attempt_is_refused_privately(self):
        self.award()
        self.free_roll(19)
        i = self.interaction()
        await self.ns["show_gamble_rules"](i)
        view = i.response.send_message.await_args.kwargs["view"]
        button = next(
            child for child in view.children
            if isinstance(child, discord.ui.Button) and child.label == "Gamble 1 coin")
        await button.callback(i)
        again = self.interaction()
        await self.ns["show_gamble_rules"](again)
        self.assertIn("one gamble per day",
                      again.response.send_message.await_args.args[0])
        self.assertTrue(again.response.send_message.await_args.kwargs["ephemeral"])


    async def test_stale_confirm_button_cannot_gamble_without_free_roll(self):
        # A previously opened private confirmation must not allow a gamble
        # if the player's free roll is absent at the point of transaction.
        self.award()
        stale_view = self.ns["GambleConfirmView"](101)
        button = next(
            child for child in stale_view.children
            if isinstance(child, discord.ui.Button)
            and child.label == "Gamble 1 coin"
        )
        i = self.interaction()
        await button.callback(i)
        i.response.defer.assert_awaited_once()
        i.followup.send.assert_awaited_once()
        self.assertIn("roll first", i.followup.send.await_args.args[0])
        self.assertTrue(i.followup.send.await_args.kwargs["ephemeral"])
        self.channel.send.assert_not_awaited()
        self.assertEqual(coin_total(self.con, 9, 101), 1)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM roll_coin_gambles").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM rolls").fetchone()[0],
            0,
        )

class SundayNoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        ensure_coin_schema(self.con)
        self.channel = FakeSundayChannel()
        self.ns = dict(
            datetime=FixedClock, TZ=UK, discord=discord,
            HYPED_GUILD_ID=9, ROLL_CHANNEL_ID=88,
            daily_coin_prize=daily_coin_prize,
            ogre_coin_emoji=lambda: "<:ogrecoin:789>",
            db=lambda: self.con,
            bot=SimpleNamespace(user=SimpleNamespace(id=999),
                                get_channel=lambda _: self.channel),
            log_error=lambda message: None, log_event=lambda message: None,
        )
        load({"announce_sunday_double_coin"}, self.ns)

    def tearDown(self):
        self.con.close()

    async def test_once_between_10_and_16_and_persistent_across_restart(self):
        previous = FixedClock.current
        try:
            FixedClock.current = datetime(2026, 11, 1, 10, 10, tzinfo=UK)
            self.assertTrue(await self.ns["announce_sunday_double_coin"]())
            self.assertTrue(await self.ns["announce_sunday_double_coin"]())
            self.channel.send.assert_awaited_once()
            self.assertIn("Sunday double coin day!",
                          self.channel.send.await_args.args[0])
            self.assertIn("2 coins", self.channel.send.await_args.args[0])
            self.assertEqual(
                self.con.execute(
                    "SELECT COUNT(*) FROM roll_sunday_bonus_notices").fetchone()[0], 1)
            load({"announce_sunday_double_coin"}, self.ns)
            self.assertTrue(await self.ns["announce_sunday_double_coin"]())
            self.channel.send.assert_awaited_once()
        finally:
            FixedClock.current = previous

    async def test_no_announcement_before_10_after_16_or_weekdays(self):
        previous = FixedClock.current
        try:
            for now in (
                datetime(2026, 11, 1, 9, 59, tzinfo=UK),
                datetime(2026, 11, 1, 16, 0, tzinfo=UK),
                datetime(2026, 11, 2, 11, 0, tzinfo=UK),
            ):
                with self.subTest(now=now):
                    FixedClock.current = now
                    self.assertTrue(await self.ns["announce_sunday_double_coin"]())
            self.channel.send.assert_not_awaited()
        finally:
            FixedClock.current = previous


if __name__ == "__main__":
    unittest.main()
