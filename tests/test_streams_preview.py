"""Guard private streamer preview and public-channel safety."""
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
from cogs.streams import TwitchPanel, StreamsCog, PREVIEW_CHANNEL_ID, PUBLIC_CHANNEL_ID, OGRE_STREAM_ICON

ROOT = Path(__file__).resolve().parents[1]


class StreamPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_suggestions_style_and_disabled_external_button(self):
        panel = TwitchPanel()
        data = json.dumps(panel.to_components())
        self.assertIn("## Live streams:", data)
        self.assertIn("Give Ogre stream name.", data)
        self.assertIn(OGRE_STREAM_ICON, data)
        self.assertIn('"label": "Twitch"', data)
        self.assertIn('"disabled": true', data)
        self.assertEqual(panel.children[0].type.value, 17)
        self.assertEqual(panel.children[1].type.value, 1)

    async def test_owner_only_private_preview_and_no_public_send(self):
        import cogs.streams as streams
        old_owner, old_guild = streams.OWNER_ID, streams.GUILD_ID
        streams.OWNER_ID, streams.GUILD_ID = 5, 6
        try:
            cog = StreamsCog(SimpleNamespace())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=5), guild_id=6,
                channel_id=PREVIEW_CHANNEL_ID,
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            await cog.preview.callback(cog, interaction)
            interaction.response.send_message.assert_awaited_once()
            kwargs = interaction.response.send_message.await_args.kwargs
            self.assertTrue(kwargs["ephemeral"])
            self.assertIsInstance(kwargs["view"], TwitchPanel)
            interaction.response.send_message.reset_mock()
            interaction.channel_id = PUBLIC_CHANNEL_ID
            await cog.preview.callback(cog, interaction)
            kwargs = interaction.response.send_message.await_args.kwargs
            self.assertNotIn("view", kwargs)
        finally:
            streams.OWNER_ID, streams.GUILD_ID = old_owner, old_guild

    async def test_extension_is_registered(self):
        source = (ROOT / "rollf.py").read_text()
        self.assertIn('await bot.load_extension("cogs.streams")', source)
