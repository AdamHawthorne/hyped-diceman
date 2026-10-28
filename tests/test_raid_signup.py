"""Offline tests of isolated raid sign-up preview and attendance schema."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import cogs.raid_signup as raid


class RaidPrototypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_attendance_fields_and_status_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with raid.database(path) as con:
                tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"characters", "raids", "signups", "gear_exports", "attendance"} <= tables)
                fields = {row[1] for row in con.execute("PRAGMA table_info(attendance)")}
                self.assertTrue({"raid_id", "discord_id", "character_id", "status", "late_minutes", "afk_minutes", "leadership_notes"} <= fields)
        self.assertEqual(set(raid.ATTENDANCE_OPTIONS), {"Pending", "Attended", "Late", "AFK", "No-show", "Excused"})
        self.assertIn("Attendance", raid.SHEET_TABS)

    async def test_private_preview_only_and_disabled_buttons(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            cog = raid.RaidPrototype(SimpleNamespace())
            response = SimpleNamespace(send_message=AsyncMock())
            interaction = SimpleNamespace(user=SimpleNamespace(id=42), guild_id=77,
                                          channel_id=raid.TEST_CHANNEL_ID, response=response)
            await cog.preview.callback(cog, interaction)
            kwargs = response.send_message.await_args.kwargs
            self.assertTrue(kwargs["ephemeral"])
            data = json.dumps(kwargs["view"].to_components())
            self.assertIn("Raid sign-up:", data)
            self.assertIn('"disabled": true', data)
            response.send_message.reset_mock()
            interaction.channel_id = raid.RAID_CHANNEL_ID
            await cog.preview.callback(cog, interaction)
            self.assertNotIn("view", response.send_message.await_args.kwargs)

    async def test_loaded_but_public_channel_not_posted(self):
        source = (Path(__file__).resolve().parents[1] / "rollf.py").read_text()
        self.assertIn('await bot.load_extension("cogs.raid_signup")', source)
