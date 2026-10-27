"""Tests for private Twitch pilot, previews and URL validation."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord

import cogs.streams as streams
from cogs.streams import TwitchPanel, TwitchLivePreview, StreamsCog, twitch_name, PREVIEW_CHANNEL_ID, PUBLIC_CHANNEL_ID, OGRE_STREAM_ICON

ROOT = Path(__file__).resolve().parents[1]


class StreamPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_twitch_panel_layout_and_live_preview_format(self):
        cog = SimpleNamespace()
        panel = TwitchPanel(cog)
        data = json.dumps(panel.to_components())
        self.assertIn("Give Ogre stream name. Me shout when live.", data)
        self.assertIn("## Streams:", data)
        self.assertIn("Watching:", data)
        self.assertIn("No Twitch streams registered yet.", data)
        self.assertNotIn("Up to 3 streamers per member.", data)
        self.assertIn(OGRE_STREAM_ICON, data)
        self.assertIn('"label": "Twitch"', data)
        self.assertNotIn('"disabled": true', data)
        self.assertEqual(panel.children[0].type.value, 17)
        self.assertEqual(panel.children[1].type.value, 1)
        live = json.dumps(TwitchLivePreview().to_components())
        self.assertIn("horriblesnore", live)
        self.assertIn("https://www.twitch.tv/horriblesnore", live)
        self.assertIn(OGRE_STREAM_ICON, live)
        self.assertNotIn("🔴", live)
        self.assertNotIn(r"\\n", live)

    async def test_twitch_url_validation(self):
        self.assertEqual(twitch_name("http://twitch.tv/horriblesnore/"), "horriblesnore")
        self.assertEqual(twitch_name("horriblesnore"), "horriblesnore")
        self.assertIsNone(twitch_name("https://evil.example/horriblesnore"))
        self.assertIsNone(twitch_name("twitch.tv/test/another"))

    async def test_private_permission_no_public_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(streams, "STREAM_DB", str(Path(tmp) / "test.db")):
                with patch.object(streams, "OWNER_ID", 5), patch.object(streams, "GUILD_ID", 6):
                    cog = StreamsCog(SimpleNamespace())
                    interaction = SimpleNamespace(
                        user=SimpleNamespace(id=5), guild_id=6, channel_id=PREVIEW_CHANNEL_ID,
                        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                        followup=SimpleNamespace(send=AsyncMock()),
                    )
                    await cog.preview.callback(cog, interaction)
                    self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
                    self.assertIsInstance(interaction.followup.send.await_args.kwargs["view"], TwitchPanel)
                    interaction.followup.send.reset_mock()
                    interaction.channel_id = PUBLIC_CHANNEL_ID
                    await cog.preview.callback(cog, interaction)
                    interaction.response.send_message.assert_awaited_once()
                    interaction.followup.send.assert_not_awaited()

    async def test_twitch_database_connections_are_closed(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(streams, "STREAM_DB", str(Path(tmp) / "closed.db")):
                cog = StreamsCog(SimpleNamespace())
                with cog.database() as con:
                    con.execute("SELECT 1").fetchone()
                with self.assertRaises(sqlite3.ProgrammingError):
                    con.execute("SELECT 1")
                (Path(tmp) / "closed.db").unlink()

    async def test_extension_is_registered(self):
        source = (ROOT / "rollf.py").read_text()
        self.assertIn('await bot.load_extension("cogs.streams")', source)
