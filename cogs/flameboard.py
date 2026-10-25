"""Hyped Flameboard Discord event listeners.

The existing engine stays in rollf.py during the first phase, avoiding any
migration of historical Flameboard rows or changes to reaction calculations.
"""
import discord
from discord.ext import commands


class FlameboardEvents(commands.Cog):
    def __init__(self, bot, guild_id, sync_message):
        self.bot = bot
        self.guild_id = guild_id
        self.sync_message = sync_message

    async def handle(self, payload):
        if payload.guild_id != self.guild_id:
            return
        if str(payload.emoji) not in ("🔥", "😂", "⭐"):
            return
        await self.sync_message(payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self.handle(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self.handle(payload)
