"""Reconcile daily player roll reactions, including after a bot restart."""
import asyncio
import re
from datetime import datetime, timezone


class RollReactions:
    def __init__(self, bot, channel_id, db, today_range, log_error, guild_id=0):
        self.bot = bot
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.db = db
        self.today_range = today_range
        self.log_error = log_error
        self.lock = asyncio.Lock()

    async def sync(self):
        # Serialize overlapping rolls so an older update cannot undo a newer one.
        async with self.lock:
            try:
                await self._sync()
            except Exception as exc:
                # Cosmetic failures must never prevent a roll or role update.
                self.log_error(f"ROLL REACTIONS FAILED: {exc!r}")

    async def _sync(self):
        start, end = self.today_range()
        with self.db() as con:
            rows = con.execute(
                "SELECT user_id, value FROM rolls WHERE actor_type = 'user' "
                "AND rolled_at >= ? AND rolled_at < ? ORDER BY rolled_at ASC, rowid ASC",
                (start, end),
            ).fetchall()
        scores = {row[0]: row[1] for row in rows}
        if not scores:
            return
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.channel_id)

        # Read real messages instead of persisting webhook response objects.
        # This also recovers today's rolls posted before this feature was deployed.
        messages = []
        seen = set()
        async for message in channel.history(
            limit=None, after=datetime.fromtimestamp(start, timezone.utc),
            before=datetime.fromtimestamp(end + 1, timezone.utc), oldest_first=True,
        ):
            if message.author.id != self.bot.user.id:
                continue
            match = re.match(r"^<@!?(\d+)> rolled (?:💯|\*\*[\d,]+\*\*) 🎲", message.content)
            if not match:
                continue
            user_id = int(match.group(1))
            if user_id in scores and user_id not in seen:
                messages.append((user_id, message))
                seen.add(user_id)

        # Rank by stored values, never by April Fools' display values.
        scores = {user_id: value for user_id, value in scores.items() if user_id in seen}
        if not scores:
            return
        high = max(scores, key=scores.get)
        low = min(scores, key=scores.get)
        guild = self.bot.get_guild(self.guild_id) if self.guild_id else None
        coin = next((e for e in guild.emojis if e.name == "ogrecoin"), None) if guild else None
        high_emoji = coin or "👑"
        for user_id, message in messages:
            for emoji, wanted in (
                (high_emoji, user_id == high),
                ("💩", user_id == low and scores[low] < scores[high]),
            ):
                present = any(str(r.emoji) == str(emoji) and r.me for r in message.reactions)
                try:
                    if wanted and not present:
                        await message.add_reaction(emoji)
                    elif present and not wanted:
                        # Remove only our reaction; leave members' reactions alone.
                        await message.remove_reaction(emoji, self.bot.user)
                except Exception as exc:
                    self.log_error(f"ROLL REACTION FAILED message={message.id}: {exc!r}")
