"""Public LFG board has no Next/Back pages and retains overflow messages."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("HYPED_GUILD_ID", "123456789012345678")

from cogs import lfg_preview as lfg


class FakeMessage:
    def __init__(self, message_id, view=None):
        self.id = message_id
        self.view = view
        self.edits = []
        self.deleted = False

    async def edit(self, **kwargs):
        self.view = kwargs.get("view")
        self.edits.append(kwargs)

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self):
        self.guild = None
        self.messages = {900: FakeMessage(900)}
        self.sent = []

    def get_partial_message(self, message_id):
        return self.messages[message_id]

    async def send(self, *, view, allowed_mentions=None):
        message_id = 1000 + len(self.sent)
        message = FakeMessage(message_id, view=view)
        self.sent.append(message)
        self.messages[message_id] = message
        return message


class BoardScrollIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(lfg, "PATH",
                               str(Path(self.temp.name) / "lfg.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        lfg.init_db()
        with lfg.connect() as db:
            db.execute(
                "INSERT INTO lfg_boards "
                "(channel_id,guild_id,message_id,board_day,page_index) "
                "VALUES (111,123456789012345678,900,'launch',3)")
            for n in range(20):
                db.execute(
                    "INSERT INTO lfg_listings "
                    "(channel_id,user_id,character,class_name,role,"
                    "availability,activity,days_off,race,notes) "
                    "VALUES (111,?,?,?,?,?,?,?,?,?)",
                    (n + 1, f"Player {n:02d}", "Warrior", "Tank",
                     "19:00 UK - 22:00 UK", "Open / Flexible",
                     "Thu 5 Nov", "Orc", f"Will farm dungeons {n}"),
                )
        self.channel = FakeChannel()
        self.bot = SimpleNamespace(get_channel=lambda _: self.channel)
        self.cog = lfg.LFGPreview(self.bot)

    async def test_all_twenty_listings_show_without_navigation(self):
        await self.cog.refresh(111)
        with lfg.connect() as db:
            overflow = db.execute(
                "SELECT position,message_id FROM lfg_board_overflow "
                "ORDER BY position").fetchall()
            row = db.execute("SELECT page_index FROM lfg_boards").fetchone()
        self.assertEqual(row["page_index"], 0)
        self.assertGreater(len(overflow), 0)
        displayed = [self.channel.messages[900]] + self.channel.sent
        rendered = [json.dumps(m.view.to_components()) for m in displayed]
        combined = "\n".join(rendered)
        for n in range(20):
            self.assertIn(f"Player {n:02d}", combined)
        self.assertNotIn("Page 1 of", combined)
        self.assertNotIn("Page 2 of", combined)
        self.assertNotIn("‹ Previous", combined)
        self.assertNotIn("Next ›", combined)
        self.assertNotIn("WGNGdr.png", combined)
        self.assertEqual(len(overflow), len(self.channel.sent))

    async def test_updates_reuse_overflow_and_remove_unused_posts(self):
        await self.cog.refresh(111)
        saved = [m.id for m in self.channel.sent]
        await self.cog.refresh(111)
        self.assertEqual([m.id for m in self.channel.sent], saved)
        with lfg.connect() as db:
            db.execute("DELETE FROM lfg_listings WHERE user_id > 2")
        await self.cog.refresh(111)
        self.assertTrue(all(m.deleted for m in self.channel.sent))
        with lfg.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM lfg_board_overflow").fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM lfg_listings").fetchone()[0], 2)
        self.assertIn("Player 00", json.dumps(
            self.channel.messages[900].view.to_components()))

    async def test_remove_dungeon_button_is_independent_of_launch_plan(self):
        button = lfg.RemoveDungeonButton(self.cog, 111)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=10), response=SimpleNamespace(
                send_message=AsyncMock()),
        )
        await button.callback(interaction)
        self.assertIn("don't have a dungeon request",
                      interaction.response.send_message.await_args.args[0])
        with lfg.connect() as db:
            db.execute(
                "INSERT INTO lfg_dungeon_requests "
                "(channel_id,user_id,character_id,dungeons,availability) "
                "VALUES (111,10,1,'Blackrock Depths','Evenings')")
        interaction.response.send_message.reset_mock()
        await button.callback(interaction)
        self.assertIsInstance(
            interaction.response.send_message.await_args.kwargs["view"],
            lfg.DungeonRemoveView,
        )


if __name__ == "__main__":
    unittest.main()
