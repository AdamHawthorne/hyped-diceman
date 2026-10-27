"""Offline tests for the private Twitch live tracker."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import cogs.streams as streams
from cogs.streams import TwitchPanel, TwitchLiveNotice, StreamsCog, twitch_name, PREVIEW_CHANNEL_ID, PUBLIC_CHANNEL_ID, OGRE_STREAM_ICON

ROOT = Path(__file__).resolve().parents[1]


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_copy_and_enabled_twitch_button(self):
        panel = TwitchPanel(SimpleNamespace(), preview=False)
        data = json.dumps(panel.to_components())
        self.assertIn("## Streams:", data)
        self.assertIn("Watching:", data)
        self.assertIn("Give Ogre stream name. Me shout when live.", data)
        self.assertIn(OGRE_STREAM_ICON, data)
        self.assertIn('"label": "Twitch"', data)
        self.assertNotIn('"disabled": true', data)
        self.assertNotIn("Up to 3 streamers per member.", data)

    async def test_real_notice_contains_direct_link_button_and_bottom_thumbnail(self):
        card = TwitchLiveNotice(
            name="horriblesnore", game="World of Warcraft",
            title="Hyped guild adventures",
            avatar_url="https://example.com/avatar.png",
            stream_thumbnail="https://example.com/live.jpg",
        )
        data = json.dumps(card.to_components())
        self.assertIn("https://www.twitch.tv/horriblesnore", data)
        self.assertIn("Watch stream", data)
        self.assertIn("https://example.com/avatar.png", data)
        self.assertIn("https://example.com/live.jpg", data)
        self.assertNotIn("🔴", data)
        self.assertEqual(len(card.children), 1)
        components = card.children[0].children
        self.assertEqual([c.type.value for c in components], [9, 12, 1])

    async def test_twitch_url_validation(self):
        self.assertEqual(twitch_name("http://twitch.tv/horriblesnore/"), "horriblesnore")
        self.assertEqual(twitch_name("horriblesnore"), "horriblesnore")
        self.assertIsNone(twitch_name("https://evil.example/horriblesnore"))
        self.assertIsNone(twitch_name("twitch.tv/test/another"))

    async def test_setup_only_in_private_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(streams, "STREAM_DB", str(Path(tmp) / "test.db")):
                with patch.object(streams, "OWNER_ID", 5), patch.object(streams, "GUILD_ID", 6):
                    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(
                        jump_url="https://discord.com/channels/6/7/8", id=8,
                    )))
                    cog = StreamsCog(SimpleNamespace(get_channel=lambda _: channel))
                    interaction = SimpleNamespace(
                        user=SimpleNamespace(id=5), guild_id=6, channel_id=PREVIEW_CHANNEL_ID,
                        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                        followup=SimpleNamespace(send=AsyncMock()),
                    )
                    await cog.setup_panel.callback(cog, interaction)
                    channel.send.assert_awaited_once()
                    panel = channel.send.await_args.kwargs["view"]
                    self.assertIsInstance(panel, TwitchPanel)
                    self.assertTrue(panel.is_persistent())
                    interaction.channel_id = PUBLIC_CHANNEL_ID
                    await cog.setup_panel.callback(cog, interaction)
                    channel.send.assert_awaited_once()
                    interaction.response.send_message.assert_awaited_once()

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

    async def test_live_poll_edits_existing_notice_without_reposting(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(streams, 'STREAM_DB', str(Path(tmp) / 'live.db')):
                edit = AsyncMock()
                channel = SimpleNamespace(
                    get_partial_message=lambda _: SimpleNamespace(edit=edit),
                    send=AsyncMock(),
                )
                cog = StreamsCog(SimpleNamespace())
                with cog.database() as con:
                    con.execute(
                        'INSERT INTO subscriptions(name, submitter, message_id, was_live) VALUES(?,?,?,?)',
                        ('horriblesnore', 5, 123, 1),
                    )
                cog.twitch_data = AsyncMock(return_value={
                    'horriblesnore': {
                        'title': 'Latest title', 'game_name': 'World of Warcraft',
                        'thumbnail_url': 'https://static-cdn.jtvnw.net/previews-ttv/live_user_horriblesnore-{width}x{height}.jpg',
                        'profile_image_url': 'https://example.com/avatar.png',
                    },
                })
                cog.channel = AsyncMock(return_value=channel)
                await cog.reconcile()
                channel.send.assert_not_awaited()
                edit.assert_awaited_once()
                result = json.dumps(edit.await_args.kwargs['view'].to_components())
                self.assertIn('Latest title', result)
                self.assertIn('ogre_refresh', result)
                self.assertEqual(cog.rows()[0][3], 123)
    async def test_offline_cleanup_removes_only_live_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(streams, "STREAM_DB", str(Path(tmp) / "offline.db")):
                delete = AsyncMock()
                channel = SimpleNamespace(get_partial_message=lambda _: SimpleNamespace(delete=delete))
                cog = StreamsCog(SimpleNamespace())
                with cog.database() as con:
                    con.execute(
                        "INSERT INTO subscriptions(name, submitter, message_id, was_live) VALUES(?,?,?,?)",
                        ("horriblesnore", 5, 123, 1),
                    )
                cog.twitch_data = AsyncMock(return_value={})
                cog.channel = AsyncMock(return_value=channel)
                await cog.reconcile()
                delete.assert_awaited_once()
                self.assertEqual(cog.rows()[0][3:], (None, 0))
                await cog.reconcile()
                delete.assert_awaited_once()

    async def test_manual_live_preview_is_ephemeral_and_does_not_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(streams, 'STREAM_DB', str(Path(tmp) / 'preview.db')):
                with patch.object(streams, 'OWNER_ID', 5), patch.object(streams, 'GUILD_ID', 6):
                    channel = SimpleNamespace(send=AsyncMock())
                    cog = StreamsCog(SimpleNamespace(get_channel=lambda _: channel))
                    cog.twitch_data = AsyncMock(return_value={})
                    interaction = SimpleNamespace(
                        user=SimpleNamespace(id=5), guild_id=6, channel_id=PREVIEW_CHANNEL_ID,
                        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                        followup=SimpleNamespace(send=AsyncMock()),
                    )
                    await cog.preview_live.callback(cog, interaction)
                    result = interaction.followup.send.await_args.kwargs
                    self.assertTrue(result['ephemeral'])
                    self.assertIsInstance(result['view'], TwitchLiveNotice)
                    channel.send.assert_not_awaited()
                    interaction.channel_id = PUBLIC_CHANNEL_ID
                    await cog.preview_live.callback(cog, interaction)
                    interaction.response.send_message.assert_awaited_once()
    async def test_extension_registered(self):
        source = (ROOT / "rollf.py").read_text()
        self.assertIn('await bot.load_extension("cogs.streams")', source)
