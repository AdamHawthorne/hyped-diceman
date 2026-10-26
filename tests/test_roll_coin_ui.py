"""Exercise real Discord components and reset orchestration without logging in."""
import ast
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock
import discord

from cogs.roll_coins import UK, ensure_coin_schema, settle_completed_days, coin_total, day_award
from cogs.roll_panels import DailyRollPanel, CoinLeaderboard, COIN_IMAGE
from cogs.ogre_voice import pick as ogre_say

SOURCE = (Path(__file__).resolve().parents[1] / 'rollf.py').read_text()


def load_functions(names, namespace):
    tree = ast.parse(SOURCE)
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'rollf.py', 'exec'), namespace)


class PanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_controls_and_inline_coin_emoji(self):
        callbacks = [AsyncMock() for _ in range(3)]
        panel = DailyRollPanel(day='2026-10-26', winner_text='Winner', tagline='Test',
                               on_roll=callbacks[0], on_leaderboard=callbacks[1], on_stats=callbacks[2])
        self.assertTrue(panel.is_persistent())
        self.assertLess(panel.total_children_count, 40)
        self.assertFalse(any(isinstance(x, discord.ui.MediaGallery) for x in panel.children))
        self.assertNotIn("Bragging rights only", json.dumps(panel.to_components()))
        self.assertNotIn("Roll day:", json.dumps(panel.to_components()))
        self.assertIn("No gold coins awarded yet.", json.dumps(panel.to_components()))
        standings = [{"rank": i, "user_id": i, "coins": 7 - i} for i in range(1, 8)]
        ranked = DailyRollPanel(day="2026-10-26", winner_text="Winner",
                                tagline="Feeling lucky today?", top_five=standings,
                                coin_emoji="<:ogrecoin:123456789>",
                                on_roll=callbacks[0], on_leaderboard=callbacks[1],
                                on_stats=callbacks[2])
        text = json.dumps(ranked.to_components())
        self.assertIn("Coin count", text)
        self.assertNotIn("First roll wins a tie.", text)
        self.assertIn("keeps the coin", text)
        self.assertLess(text.index("Hyped Rolls"), text.index("Coin count"))
        self.assertIn("keeps the coin", text)
        self.assertNotIn("`/leaderboard`", text)
        self.assertNotIn("`/stats`", text)
        for i in range(1, 6):
            self.assertIn(f"<@{i}>", text)
        self.assertNotIn("<@6>", text)
        self.assertIn("Feeling lucky today?", text)
        self.assertIn("<:ogrecoin:123456789>", text)
        self.assertNotIn("Yesterday\'s gold coin", text)
        buttons = [x for x in panel.walk_children() if isinstance(x, discord.ui.Button)]
        self.assertEqual(len({x.custom_id for x in buttons}), 3)
        self.assertEqual(buttons[0].style, discord.ButtonStyle.success)
        self.assertEqual(str(buttons[0].emoji), "🎲")
        for button, callback in zip(buttons, callbacks):
            await button.callback(None)
            callback.assert_awaited_once()
        preview = DailyRollPanel(day='2026-10-26', winner_text='Winner', tagline='Test',
                                  on_roll=callbacks[0], on_leaderboard=callbacks[1], on_stats=callbacks[2],
                                  preview=True)
        self.assertTrue(all(x.disabled for x in preview.walk_children() if isinstance(x, discord.ui.Button)))

    async def test_ogre_winner_displays_real_bot_mention(self):
        callbacks = [AsyncMock() for _ in range(3)]
        standings = [{"rank": 1, "user_id": 0, "coins": 2}]
        panel = DailyRollPanel(
            day="2026-10-26", winner_text="", tagline="Test",
            on_roll=callbacks[0], on_leaderboard=callbacks[1], on_stats=callbacks[2],
            top_five=standings, ogre_id=123456, coin_emoji="<:ogrecoin:789>",
        )
        contents = json.dumps(panel.to_components())
        self.assertIn("<@123456>", contents)
        self.assertNotIn("<@0>", contents)
        board = CoinLeaderboard(standings, owner_id=7, ogre_id=123456)
        self.assertIn("<@123456>", json.dumps(board.to_components()))

    async def test_leaderboard_pagination_ownership_and_empty_state(self):
        rows = [{'rank': i, 'user_id': i, 'coins': 40 - i} for i in range(1, 37)]
        panel = CoinLeaderboard(rows, owner_id=7)
        buttons = [x for x in panel.walk_children() if isinstance(x, discord.ui.Button)]
        self.assertTrue(buttons[0].disabled)
        interaction = SimpleNamespace(user=SimpleNamespace(id=7), response=AsyncMock())
        await buttons[1].callback(interaction)
        next_view = interaction.response.edit_message.call_args.kwargs['view']
        self.assertEqual(next_view.page, 1)
        self.assertLess(next_view.total_children_count, 40)
        interaction.user.id = 8
        self.assertFalse(await next_view.interaction_check(interaction))
        empty = CoinLeaderboard([], owner_id=7)
        self.assertIn('No gold coins', json.dumps(empty.to_components()))


class HighTieReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_later_joint_high_gets_silent_reply(self):
        con = sqlite3.connect(":memory:", isolation_level=None)
        try:
            con.execute("CREATE TABLE rolls (user_id INTEGER, value INTEGER, actor_type TEXT, rolled_at INTEGER)")
            start = int(datetime(2026, 10, 26, tzinfo=UK).timestamp())
            con.execute("INSERT INTO rolls VALUES (11, 92, 'user', ?)", (start + 60,))
            con.execute("INSERT INTO rolls VALUES (22, 92, 'user', ?)", (start + 90,))
            response = SimpleNamespace(reply=AsyncMock())
            interaction = SimpleNamespace(user=SimpleNamespace(id=22),
                                          original_response=AsyncMock(return_value=response))
            ns = dict(db=lambda: con, today_range=lambda: (start, start+86400),
                      bot=SimpleNamespace(user=SimpleNamespace(id=999)),
                      ogre_say=ogre_say, discord=discord,
                      log_error=lambda message: None)
            load_functions({"reply_if_tied_for_highest"}, ns)
            await ns["reply_if_tied_for_highest"](interaction, 92)
            response.reply.assert_awaited_once()
            args, kwargs = response.reply.call_args
            self.assertIn("<@11>", args[0])
            self.assertFalse(kwargs["mention_author"])
            self.assertFalse(kwargs["allowed_mentions"].users)
            response.reply.reset_mock()
            await ns["reply_if_tied_for_highest"](interaction, 91, response)
            response.reply.assert_not_awaited()
            con.execute("INSERT INTO rolls VALUES (33, 99, 'user', ?)", (start + 95,))
            await ns["reply_if_tied_for_highest"](interaction, 92, response)
            response.reply.assert_not_awaited()
        finally:
            con.close()


class WinnerMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_winner_text_uses_weekday_and_guild_coin_emoji(self):
        con = sqlite3.connect(":memory:", isolation_level=None)
        try:
            ensure_coin_schema(con)
            con.execute(
                """INSERT INTO roll_coin_awards
                (guild_id, roll_date, user_id, username, winning_roll, source, awarded_at)
                VALUES (9, '2026-10-26', 42, 'roller', 97, 'rolls', 1)"""
            )
            ns = dict(
                datetime=datetime, timedelta=timedelta, TZ=UK, HYPED_GUILD_ID=9,
                db=lambda: con, day_award=day_award, coin_total=coin_total,
                ogre_coin_emoji=lambda: "<:ogrecoin:12345>",
                ogre_say=ogre_say,
                bot=SimpleNamespace(user=SimpleNamespace(id=555)),
            )
            load_functions({"coin_winner_message", "daily_ogre_tagline"}, ns)
            msg = ns["coin_winner_message"](datetime(2026, 10, 27, tzinfo=UK).date())
            self.assertIn("**Coin winner!**", msg)
            self.assertIn("**Monday**", msg)
            self.assertIn("<@42>", msg)
            self.assertIn("**97**", msg)
            self.assertIn("<:ogrecoin:12345>", msg)
            self.assertNotIn("2026-10-26", msg)
            self.assertIn("\n**1 coin** now.", msg)
            self.assertNotIn("**1 coins**", msg)
            day = datetime(2026, 10, 27, tzinfo=UK).date()
            self.assertEqual(ns["daily_ogre_tagline"](day), ns["daily_ogre_tagline"](day))
            self.assertGreater(len({ns["daily_ogre_tagline"](day + timedelta(days=i)) for i in range(30)}), 1)
        finally:
            con.close()

    async def test_midnight_announcement_is_once_only_and_daytime_is_silent(self):
        con = sqlite3.connect(":memory:", isolation_level=None)
        try:
            channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=888)))
            class Clock:
                current = datetime(2026, 10, 27, 0, 2, tzinfo=UK)

                @classmethod
                def now(cls, timezone):
                    return cls.current

            ns = dict(
                datetime=Clock, timedelta=timedelta, TZ=UK, HYPED_GUILD_ID=9,
                db=lambda: con, discord=discord,
                coin_winner_message=lambda day: "Coin winner!",
                log_error=lambda message: None,
            )
            load_functions({"announce_coin_winner"}, ns)
            day = Clock.current.date()
            await ns["announce_coin_winner"](channel, day)
            await ns["announce_coin_winner"](channel, day)
            channel.send.assert_awaited_once()
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM roll_coin_announcements").fetchone()[0], 1
            )
            Clock.current = datetime(2026, 10, 28, 12, 0, tzinfo=UK)
            await ns["announce_coin_winner"](channel, Clock.current.date())
            channel.send.assert_awaited_once()
        finally:
            con.close()


class ResetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.con = sqlite3.connect(':memory:', isolation_level=None)
        self.con.execute('CREATE TABLE rolls (user_id, username, value, rolled_at, actor_type)')
        self.con.execute('CREATE TABLE daily_reset_posts (roll_date PRIMARY KEY, channel_id, message_id)')
        ensure_coin_schema(self.con)
        now = datetime.now(UK)
        stamp = int((now - timedelta(days=1)).replace(hour=12).timestamp())
        self.con.execute("INSERT INTO rolls VALUES (1, 'winner', 99, ?, 'user')", (stamp,))
        self.message = SimpleNamespace(id=100, author=SimpleNamespace(id=999), edit=AsyncMock(), components=[])
        self.channel = SimpleNamespace(id=55, fetch_message=AsyncMock(return_value=self.message),
                                       send=AsyncMock(return_value=self.message))
        async def history(**kwargs):
            for message in self.history_messages:
                yield message
        self.history_messages = []
        self.channel.history = history
        self.ns = dict(asyncio=asyncio, datetime=datetime, timedelta=timedelta, TZ=UK,
                       json=json, discord=discord, DAILY_POST_LOCK=asyncio.Lock(),
                       HYPED_GUILD_ID=9, ROLL_CHANNEL_ID=55,
                       bot=SimpleNamespace(get_channel=lambda _: self.channel,
                                           user=SimpleNamespace(id=999)),
                       db=lambda: self.con, settle_completed_days=settle_completed_days,
                       log_event=lambda _: None, log_error=lambda _: None,
                       build_daily_roll_panel=lambda **kwargs: None,
                       announce_coin_winner=AsyncMock())
        load_functions({'post_daily_reset_message'}, self.ns)

    async def asyncTearDown(self):
        self.con.close()

    async def test_concurrent_reset_and_restart_do_not_duplicate_coin_or_post(self):
        post = self.ns['post_daily_reset_message']
        await asyncio.gather(post(), post())
        await post()
        self.assertEqual(coin_total(self.con, 9, 1), 1)
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.message.edit.await_count, 0)
        self.assertEqual(self.ns["announce_coin_winner"].await_count, 3)

    async def test_send_failure_keeps_one_award_and_retry_can_post(self):
        self.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Denied'), 'Denied')
        self.assertIsNone(await self.ns['post_daily_reset_message']())
        self.assertEqual(coin_total(self.con, 9, 1), 1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM daily_reset_posts').fetchone()[0], 0)
        self.channel.send.side_effect = None
        self.assertTrue(await self.ns['post_daily_reset_message']())
        self.assertEqual(coin_total(self.con, 9, 1), 1)

    async def test_existing_legacy_daily_message_is_upgraded_in_place(self):
        day = datetime.now(UK).date().isoformat()
        self.con.execute('INSERT INTO daily_reset_posts VALUES (?, 55, 100)', (day,))
        await self.ns['post_daily_reset_message']()
        self.channel.send.assert_not_awaited()
        self.message.edit.assert_not_awaited()
        await self.ns['post_daily_reset_message'](refresh_existing=True)
        self.assertEqual(self.message.edit.call_args.kwargs['embeds'], [])
        self.assertEqual(coin_total(self.con, 9, 1), 1)

    async def test_recover_sent_message_after_missing_database_write(self):
        marker = "Hyped Rolls · Daily Reset"
        component = SimpleNamespace(to_dict=lambda: {'content': marker})
        self.message.components = [component]
        self.message.created_at = datetime.now(UK)
        self.history_messages = [self.message]
        await self.ns['post_daily_reset_message']()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.con.execute('SELECT message_id FROM daily_reset_posts').fetchone()[0], 100)

    async def test_preview_and_manual_test_post_do_not_award_or_reset(self):
        self.ns.update(ADMIN_GUILD_ID=9, OWNER_ID=7, app_commands=discord.app_commands)
        load_functions({'previewroll_admin', 'postroll_admin'}, self.ns)
        interaction = SimpleNamespace(user=SimpleNamespace(id=7), guild_id=9, channel_id=88,
                                      channel=self.channel, response=AsyncMock(), followup=AsyncMock())
        await self.ns['previewroll_admin'](interaction)
        await self.ns['postroll_admin'](interaction)
        self.assertEqual(coin_total(self.con, 9, 1), 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM daily_reset_posts').fetchone()[0], 0)

    async def test_previewcoin_is_private_and_does_not_award_or_post(self):
        self.ns.update(
            ADMIN_GUILD_ID=9, OWNER_ID=7, app_commands=discord.app_commands,
            coin_winner_message=lambda day: "**Coin winner!** <@42> had Monday's roll.",
        )
        load_functions({"previewcoin_admin"}, self.ns)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7), guild_id=9, response=AsyncMock()
        )
        await self.ns["previewcoin_admin"](interaction)
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        self.assertIn(
            "**Coin winner!**",
            interaction.response.send_message.call_args.args[0],
        )
        self.channel.send.assert_not_awaited()
        self.assertEqual(coin_total(self.con, 9, 1), 0)

    async def test_test_channel_roll_button_is_blocked(self):
        self.ns['perform_roll'] = AsyncMock()
        load_functions({'daily_roll_action'}, self.ns)
        interaction = SimpleNamespace(guild_id=9, channel_id=88, response=AsyncMock())
        await self.ns['daily_roll_action'](interaction)
        self.ns['perform_roll'].assert_not_awaited()
