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
                "SELECT user_id, value FROM rolls WHERE actor_type IN ('user', 'gamble', 'bot') "
                "AND rolled_at >= ? AND rolled_at < ? ORDER BY rolled_at ASC, rowid ASC",
                (start, end),
            ).fetchall()
        # A lower gamble roll must never replace the member's existing best.
        all_scores = {}
        for user_id, value in rows:
            all_scores[user_id] = max(value, all_scores.get(user_id, 0))
        scores = {uid: value for uid, value in all_scores.items() if uid != 0}
        if not scores:
            return
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.channel_id)

        # Read real messages instead of persisting webhook response objects.
        # This also recovers today's rolls posted before this feature was deployed.
        messages = []
        latest_by_user = {}
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
            if user_id in scores:
                messages.append((user_id, message))
                latest_by_user[user_id] = message.id

        # Only visible human roll messages count for reaction ranking.
        # Keep Ogre's saved roll even though his message has no user mention.
        all_scores = {user_id: value for user_id, value in all_scores.items()
                      if user_id == 0 or user_id in latest_by_user}
        # Rank by stored values, never by April Fools' display values.
        scores = {user_id: value for user_id, value in scores.items() if user_id in latest_by_user}
        if not scores:
            return
        high = max(scores, key=scores.get)
        # Ogre participates in the low-roll comparison, but never gets poop.
        # If Ogre is lowest (including a tie), remove poop from all humans.
        lowest_value = min(all_scores.values())
        human_low = min(scores, key=scores.get)
        low = human_low if scores[human_low] == lowest_value and (
            0 not in all_scores or all_scores[0] > lowest_value
        ) else None
        guild = self.bot.get_guild(self.guild_id) if self.guild_id and hasattr(self.bot, 'get_guild') else None
        coin = next((e for e in guild.emojis if e.name == "ogrecoin"), None) if guild else None
        high_emoji = coin or "<:ogrecoin:1564336079430099035>"
        for user_id, message in messages:
            # Retire the old crown reaction without touching member reactions.
            for reaction in message.reactions:
                if str(reaction.emoji) == "👑" and reaction.me:
                    try:
                        await message.remove_reaction("👑", self.bot.user)
                    except Exception as exc:
                        self.log_error(f"ROLL REACTION CLEANUP FAILED message={message.id}: {exc!r}")
            for emoji, wanted in (
                (high_emoji, user_id == high and message.id == latest_by_user[user_id]),
                ("💩", low is not None and user_id == low
                 and scores[low] < max(all_scores.values())
                 and message.id == latest_by_user[user_id]),
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
