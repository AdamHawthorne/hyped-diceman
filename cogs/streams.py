"""Private #ogre Twitch pilot: signup, moderation and live-only notices."""
import asyncio
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PREVIEW_CHANNEL_ID = 1564594901746716713
PUBLIC_CHANNEL_ID = 1562121249176424619  # Reserved; never posted to during pilot
OGRE_STREAM_ICON = "https://i.vgy.me/DSSi6n.png"
ACCENT = 0x8C5A3C
STREAM_DB = os.getenv("STREAM_DB_PATH", "/data/streams.db" if os.path.isdir("/data") else "streams.db")
MAX_PER_MEMBER = 3


def twitch_name(value):
    value = value.strip().lower().rstrip("/")
    match = re.fullmatch(r"(?:https?://)?(?:www\.)?twitch\.tv/([a-z0-9_]{4,25})", value)
    if match:
        return match.group(1)
    return value if re.fullmatch(r"[a-z0-9_]{4,25}", value) else None


class TwitchPanel(discord.ui.LayoutView):
    def __init__(self, cog=None, *, preview=True, streams=()):
        super().__init__(timeout=300 if preview else None)
        entries = [
            f"**[{name}](https://www.twitch.tv/{name})** · {status}"
            for name, status in streams
        ]
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "## Streams:\n"
                    "Give Ogre stream name. Me shout when live."
                ),
                accessory=discord.ui.Thumbnail(
                    OGRE_STREAM_ICON, description="Hyped Ogre livestream icon",
                ),
            ),
            discord.ui.TextDisplay(
                "### Watching:\n"
                + ("\n".join(entries) if entries else "-# No Twitch streams registered yet.")
            ),
            accent_color=ACCENT,
        ))
        self.add_item(discord.ui.ActionRow(TwitchRegisterButton(cog)))


class TwitchLiveNotice(discord.ui.LayoutView):
    """Live Twitch notice using real metadata when available."""
    def __init__(self, *, name, title, game, avatar_url=None, stream_thumbnail=None):
        super().__init__(timeout=None)
        content = discord.ui.TextDisplay(
            "## Ogre spot stream!\n"
            f"**{discord.utils.escape_markdown(name)}** streaming on Twitch!\n\n"
            f"**Playing:** {discord.utils.escape_mentions(discord.utils.escape_markdown(game or 'Unknown'))}\n"
            f"**Stream:** {discord.utils.escape_markdown(title or 'Live on Twitch')}\n\n"
            f"[twitch.tv/{name}](https://www.twitch.tv/{name})\n"
            "Me shout! Go have look!"
        )
        if avatar_url and avatar_url.startswith("https://"):
            self.add_item(discord.ui.Container(
                discord.ui.Section(content, accessory=discord.ui.Thumbnail(avatar_url)),
                accent_color=0x9146FF,
            ))
        else:
            self.add_item(discord.ui.Container(
                discord.ui.Section(content, accessory=discord.ui.Thumbnail(OGRE_STREAM_ICON)),
                accent_color=0x9146FF,
            ))
        # Put the link button within the V2 notification container.
        self.children[0].add_item(discord.ui.ActionRow(discord.ui.Button(
            label="Watch stream", style=discord.ButtonStyle.link,
            url=f"https://www.twitch.tv/{name}",
        )))
        # Stream preview is the last item in the card, under the button.
        if stream_thumbnail and stream_thumbnail.startswith("https://"):
            self.children[0].add_item(discord.ui.MediaGallery(
                discord.MediaGalleryItem(media=stream_thumbnail, description="Live Twitch stream thumbnail"),
            ))


class TwitchRegistration(discord.ui.Modal, title="Register Twitch streamer"):
    streamer = discord.ui.TextInput(
        label="Twitch name or URL", placeholder="twitch.tv/horriblesnore",
        max_length=100, min_length=4,
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction):
        await self.cog.register(interaction, str(self.streamer.value))


class TwitchRegisterButton(discord.ui.Button):
    def __init__(self, cog):
        super().__init__(label="Twitch", style=discord.ButtonStyle.secondary,
                         custom_id="hyped_streams_twitch_v1", disabled=cog is None)
        self.cog = cog

    async def callback(self, interaction):
        if interaction.guild_id != GUILD_ID or interaction.channel_id != PREVIEW_CHANNEL_ID:
            await interaction.response.send_message("Private #ogre testing only.", ephemeral=True)
            return
        await interaction.response.send_modal(TwitchRegistration(self.cog))


class RemoveTwitchButton(discord.ui.Button):
    def __init__(self, cog, name):
        super().__init__(label="Remove streamer", style=discord.ButtonStyle.danger,
                         custom_id=f"hyped_stream_remove_{name}")
        self.cog = cog
        self.name = name

    async def callback(self, interaction):
        if interaction.guild_id != GUILD_ID or interaction.channel_id != PREVIEW_CHANNEL_ID:
            await interaction.response.send_message("Private testing only.", ephemeral=True)
            return
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.id == OWNER_ID):
            await interaction.response.send_message("Only leadership can remove streamers.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.cog.remove_streamer(self.name)
        await interaction.followup.send(f"Removed **{self.name}** from Ogre's watchlist.", ephemeral=True)


class RemoveTwitchView(discord.ui.View):
    def __init__(self, cog, name):
        super().__init__(timeout=None)
        self.add_item(RemoveTwitchButton(cog, name))


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class StreamsCog(commands.GroupCog, group_name="streams",
                 group_description="Hyped Twitch private pilot"):
    def __init__(self, bot):
        self.bot = bot
        self.task = None
        self.token = None
        self.token_expires = 0
        self.client_id = os.getenv("TWITCH_CLIENT_ID", "")
        self.client_secret = os.getenv("TWITCH_CLIENT_SECRET", "")
        os.makedirs(os.path.dirname(os.path.abspath(STREAM_DB)), exist_ok=True)
        with self.database() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
                name TEXT PRIMARY KEY, submitter INTEGER NOT NULL,
                last_live INTEGER, message_id INTEGER, was_live INTEGER NOT NULL DEFAULT 0
            )""")

    @contextmanager
    def database(self):
        # sqlite3's built-in context manager commits but does not close.
        # Explicit closure avoids locked Twitch DB files on Windows and restarts.
        con = sqlite3.connect(STREAM_DB, timeout=20)
        try:
            with con:
                yield con
        finally:
            con.close()

    def rows(self):
        with self.database() as con:
            return con.execute(
                "SELECT name, submitter, last_live, message_id, was_live FROM subscriptions ORDER BY name"
            ).fetchall()

    async def cog_load(self):
        self.bot.add_view(TwitchPanel(self, preview=False, streams=[]))
        for name, *_ in self.rows():
            self.bot.add_view(RemoveTwitchView(self, name))
        self.task = asyncio.create_task(self.watch_loop())

    def cog_unload(self):
        if self.task:
            self.task.cancel()

    async def channel(self):
        return self.bot.get_channel(PREVIEW_CHANNEL_ID) or await self.bot.fetch_channel(PREVIEW_CHANNEL_ID)

    async def register(self, interaction, text):
        name = twitch_name(text)
        if not name:
            await interaction.response.send_message("Use a Twitch channel name or twitch.tv link.", ephemeral=True)
            return
        with self.database() as con:
            count = con.execute("SELECT COUNT(*) FROM subscriptions WHERE submitter=?", (interaction.user.id,)).fetchone()[0]
            existing = con.execute("SELECT 1 FROM subscriptions WHERE name=?", (name,)).fetchone()
            if existing:
                outcome = "Ogre already watching that streamer."
            elif count >= MAX_PER_MEMBER:
                outcome = "Ogre already watching three streamers for you."
            else:
                con.execute("INSERT INTO subscriptions(name,submitter) VALUES(?,?)", (name, interaction.user.id))
                outcome = None
        if outcome:
            await interaction.response.send_message(outcome, ephemeral=True)
            return
        self.bot.add_view(RemoveTwitchView(self, name))
        ready = bool(self.client_id and self.client_secret)
        acknowledgement = (f"Ogre watching **{name}** now. ✅" if ready else
                           f"Registered **{name}**. Twitch API keys needed before live alerts work.")
        await interaction.response.send_message(acknowledgement, ephemeral=True)
        await (await self.channel()).send(
            f"**Twitch added** · [{name}](https://twitch.tv/{name}) · submitted by <@{interaction.user.id}>",
            view=RemoveTwitchView(self, name),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def remove_streamer(self, name):
        with self.database() as con:
            row = con.execute("SELECT message_id FROM subscriptions WHERE name=?", (name,)).fetchone()
            con.execute("DELETE FROM subscriptions WHERE name=?", (name,))
        if row and row[0]:
            try:
                await (await self.channel()).get_partial_message(row[0]).delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    async def twitch_data(self):
        if not self.client_id or not self.client_secret:
            return None
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if not self.token or time.time() >= self.token_expires - 120:
                async with session.post(
                    "https://id.twitch.tv/oauth2/token",
                    params={"client_id": self.client_id, "client_secret": self.client_secret,
                            "grant_type": "client_credentials"},
                ) as response:
                    response.raise_for_status()
                    auth = await response.json()
                self.token = auth["access_token"]
                self.token_expires = time.time() + auth["expires_in"]
            names = [row[0] for row in self.rows()]
            if not names:
                return {}
            result = {}
            headers = {"Client-ID": self.client_id, "Authorization": f"Bearer {self.token}"}
            for i in range(0, len(names), 100):
                params = [("user_login", name) for name in names[i:i+100]]
                async with session.get("https://api.twitch.tv/helix/streams", params=params, headers=headers) as response:
                    response.raise_for_status()
                    data = await response.json()
                for stream in data.get("data", []):
                    result[stream["user_login"].lower()] = stream
            # Twitch stream payloads do not contain profile images.
            # Fetch avatars separately, using the same app token.
            for i in range(0, len(names), 100):
                params = [("login", name) for name in names[i:i+100]]
                async with session.get("https://api.twitch.tv/helix/users",
                                       params=params, headers=headers) as response:
                    response.raise_for_status()
                    profiles = await response.json()
                for user in profiles.get("data", []):
                    key = user["login"].lower()
                    if key in result:
                        result[key]["profile_image_url"] = user.get("profile_image_url")
            return result

    async def reconcile(self):
        live = await self.twitch_data()
        if live is None:
            return
        channel = await self.channel()
        for name, submitter, last_live, message_id, was_live in self.rows():
            info = live.get(name)
            if info:
                if not message_id:
                    message = await channel.send(
                        view=TwitchLiveNotice(name=name, title=info.get("title") or "",
                                               game=info.get("game_name") or "",
                                               avatar_url=info.get("profile_image_url"),
                                               stream_thumbnail=(info.get("thumbnail_url") or "").replace("{width}", "640").replace("{height}", "360")),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    message_id = message.id
                with self.database() as con:
                    con.execute(
                        "UPDATE subscriptions SET was_live=1, message_id=?, last_live=? WHERE name=?",
                        (message_id, int(time.time()), name),
                    )
            else:
                if message_id:
                    try:
                        await channel.get_partial_message(message_id).delete()
                    except discord.NotFound:
                        pass
                    except (discord.Forbidden, discord.HTTPException):
                        continue
                with self.database() as con:
                    con.execute(
                        "UPDATE subscriptions SET was_live=0, message_id=NULL WHERE name=?", (name,)
                    )

    async def watch_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("TWITCH PRIVATE PILOT SYNC FAILED")
            await asyncio.sleep(120)

    async def _permission(self, interaction):
        if (interaction.user.id != OWNER_ID or interaction.guild_id != GUILD_ID
                or interaction.channel_id != PREVIEW_CHANNEL_ID):
            await interaction.response.send_message(
                "Ogre only show this in private #ogre to owner.", ephemeral=True
            )
            return False
        return True

    @app_commands.command(name="setup", description="Post Twitch registration in private #ogre")
    async def setup_panel(self, interaction: discord.Interaction):
        if not await self._permission(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        streams = []
        for name, _, last_live, _, was_live in self.rows():
            status = "Live" if was_live else (
                f"Last live: <t:{last_live}:R>" if last_live else "Offline"
            )
            streams.append((name, status))
        try:
            message = await (await self.channel()).send(
                view=TwitchPanel(self, preview=False, streams=streams),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await interaction.followup.send(
                f"Ogre posted the Twitch sign-up panel: {message.jump_url}",
                ephemeral=True,
            )
        except (discord.Forbidden, discord.HTTPException):
            logging.exception("STREAMS SETUP FAILED")
            await interaction.followup.send("Ogre cannot post in private #ogre.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(StreamsCog(bot))
