"""Owner-only countdown and repeating cooldown with independent SQLite storage.

One public V2 timer per channel. Preview is private; reminders are owner DMs.
"""
import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands

from .countdown_core import (
    UK, parse_uk_datetime, validate_image_url, upcoming_string, render_key,
    next_cycle, pre_reminder_due, due_reminder_due,
)

DB_PATH = os.getenv(
    "COUNTDOWN_DB_PATH",
    "/data/countdown.db" if Path("/data").is_dir() else "countdown.db",
)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
ACCENT = 0x8C5A3C


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def init_db():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS countdowns (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            target_ts INTEGER NOT NULL,
            image_url TEXT NOT NULL DEFAULT '',
            remind_minutes INTEGER NOT NULL DEFAULT 0,
            repeat_seconds INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            pre_notified_target INTEGER NOT NULL DEFAULT 0,
            due_notified_target INTEGER NOT NULL DEFAULT 0,
            last_render_key TEXT NOT NULL DEFAULT ''
        )""")


def get_timer(channel_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM countdowns WHERE channel_id=?", (channel_id,)
        ).fetchone()
    return dict(row) if row else None


def validate_settings(title, when_uk, icon_url, reminder, repeat_hours, now):
    title = " ".join(title.split())
    if not 1 <= len(title) <= 100:
        raise ValueError("Title must be 1 to 100 characters.")
    target = parse_uk_datetime(when_uk)
    if target <= now:
        raise ValueError("Choose a future UK date and time.")
    if not 0 <= reminder <= 10080:
        raise ValueError("Reminder must be 0 to 10080 minutes.")
    if not 0 <= repeat_hours <= 8760:
        raise ValueError("Repeat must be 0 to 8760 hours.")
    return dict(
        title=title, target_ts=target, image_url=validate_image_url(icon_url),
        remind_minutes=reminder, repeat_seconds=repeat_hours * 3600, active=1,
    )


def timestamp_label(target):
    return f"<t:{target}:F> · <t:{target}:R>"


class CountdownLayout(discord.ui.LayoutView):
    """Hyped-themed V2 countdown, with a small icon on the right."""

    def __init__(self, config, *, now=None, preview=False):
        super().__init__(timeout=900 if preview else None)
        now = int(time.time()) if now is None else now
        target = int(config["target_ts"])
        repeat = config["repeat_seconds"]
        active = config["active"]
        if not active:
            label, status = "Paused", "⏸️ Paused"
        elif now >= target and not repeat:
            label, status = "READY!", "✅ Complete"
        else:
            label, status = upcoming_string(target, now), "⏱️ Counting down"

        # Before the user uploads the converted PNG to vgy, show a
        # right-hand stopwatch emoji instead of an incorrect placeholder PNG.
        accessory = (
            discord.ui.Thumbnail(
                config["image_url"], description="Hyped stopwatch icon",
            )
            if config["image_url"] else
            discord.ui.Button(
                emoji="⏱️", style=discord.ButtonStyle.secondary, disabled=True,
            )
        )
        header = discord.ui.Section(
            discord.ui.TextDisplay(
                "## <Hyped> · Countdown / Cooldown\n"
                f"**{discord.utils.escape_markdown(config['title'])}**\n"
                f"-# {status}" + (" · DEMO PREVIEW" if preview else "")
            ),
            accessory=accessory,
        )
        panel = discord.ui.Container(header, accent_color=ACCENT)
        repeat_text = (
            f"Repeats every {repeat // 3600}h" if repeat else "One-time countdown"
        )
        reminder_text = (
            f"Owner DM {config['remind_minutes']}m before and at zero"
            if config["remind_minutes"] else "Owner DM when ready"
        )
        panel.add_item(discord.ui.TextDisplay(
            f"## {label}\n"
            f"**Target:** {timestamp_label(target)}\n"
            f"-# {repeat_text} · {reminder_text}"
        ))
        self.add_item(panel)
        self.add_item(discord.ui.TextDisplay(
            "-# Hyped Ogre · Automatically updated every minute · UK time"
        ))


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class CountdownCog(
    commands.GroupCog, group_name="countdown",
    group_description="Owner-only timer setup, preview and management",
):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.task = None

    async def cog_load(self):
        init_db()
        self.task = asyncio.create_task(self._run(), name="hyped-countdown")

    def cog_unload(self):
        if self.task:
            self.task.cancel()

    async def interaction_check(self, interaction):
        if (
            not OWNER_ID or interaction.user.id != OWNER_ID
            or interaction.guild_id != GUILD_ID
        ):
            await interaction.response.send_message(
                "Only the Hyped Ogre owner can configure countdowns.",
                ephemeral=True,
            )
            return False
        return True

    @app_commands.command(name="preview", description="Preview a V2 timer privately")
    @app_commands.describe(
        title="Countdown title",
        when_uk="YYYY-MM-DD HH:MM in UK time; omit for demo two hours away",
        icon_url="HTTPS PNG link once uploaded to vgy.me",
        remind_minutes="DM warning before target; 0 only at zero",
        repeat_hours="0 for one-off; positive hours for repeating cooldown",
    )
    async def preview(
        self, interaction: discord.Interaction,
        title: str = "Hyped Countdown", when_uk: str | None = None,
        icon_url: str | None = None, remind_minutes: int = 30,
        repeat_hours: int = 0,
    ):
        now = int(time.time())
        if not when_uk:
            moment = datetime.fromtimestamp(now, UK) + timedelta(hours=2)
            when_uk = moment.strftime("%Y-%m-%d %H:%M")
        try:
            config = validate_settings(
                title, when_uk, icon_url, remind_minutes, repeat_hours, now,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            view=CountdownLayout(config, now=now, preview=True),
            ephemeral=True,
        )

    @app_commands.command(name="post", description="Post or edit this channel's public V2 timer")
    @app_commands.describe(
        title="Public countdown title",
        when_uk="YYYY-MM-DD HH:MM in Europe/London time",
        icon_url="HTTPS image URL from vgy.me; optional and remembered",
        remind_minutes="Owner DM lead in minutes; 0 for only at zero",
        repeat_hours="Cooldown repeats every N hours; 0 is one-off",
    )
    async def post(
        self, interaction: discord.Interaction, title: str, when_uk: str,
        icon_url: str | None = None, remind_minutes: int = 30,
        repeat_hours: int = 0,
    ):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use a Hyped server text channel.", ephemeral=True,
            )
            return
        existing = get_timer(channel.id)
        now = int(time.time())
        try:
            config = validate_settings(
                title, when_uk,
                icon_url if icon_url is not None else
                (existing["image_url"] if existing else ""),
                remind_minutes, repeat_hours, now,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        async with self.lock:
            message = None
            view = CountdownLayout(config, now=now)
            try:
                if existing:
                    try:
                        message = await channel.fetch_message(existing["message_id"])
                    except discord.NotFound:
                        pass
                if message is not None:
                    await message.edit(
                        content=None, embeds=[], attachments=[], view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    message = await channel.send(
                        view=view, allowed_mentions=discord.AllowedMentions.none(),
                    )
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"COUNTDOWN POST FAILED channel={channel.id}: {exc!r}")
                await interaction.followup.send(
                    "Couldn't publish the timer. Check View Channel, Send "
                    "Messages and Embed Links permissions.", ephemeral=True,
                )
                return

            with connect() as db:
                db.execute(
                    """INSERT INTO countdowns (
                        channel_id, guild_id, message_id, owner_id, title,
                        target_ts, image_url, remind_minutes, repeat_seconds,
                        active, pre_notified_target, due_notified_target,
                        last_render_key
                    ) VALUES (?,?,?,?,?,?,?,?,?,1,0,0,?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        guild_id=excluded.guild_id,
                        message_id=excluded.message_id,
                        owner_id=excluded.owner_id,
                        title=excluded.title,
                        target_ts=excluded.target_ts,
                        image_url=excluded.image_url,
                        remind_minutes=excluded.remind_minutes,
                        repeat_seconds=excluded.repeat_seconds,
                        active=1,
                        pre_notified_target=0,
                        due_notified_target=0,
                        last_render_key=excluded.last_render_key""",
                    (
                        channel.id, interaction.guild_id, message.id,
                        interaction.user.id, config["title"], config["target_ts"],
                        config["image_url"], config["remind_minutes"],
                        config["repeat_seconds"],
                        render_key(config["target_ts"], now, 1),
                    ),
                )
        await interaction.followup.send(
            f"✅ Public V2 countdown ready: {message.jump_url}\n"
            "The bot edits that same post and DMs reminders to the owner.",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show this channel's timer settings")
    async def status(self, interaction: discord.Interaction):
        row = get_timer(interaction.channel_id)
        if row is None:
            await interaction.response.send_message(
                "No countdown saved here.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"**{discord.utils.escape_markdown(row['title'])}**\n"
            f"Status: {'Running' if row['active'] else 'Stopped'}\n"
            f"Target: {timestamp_label(row['target_ts'])}\n"
            f"Reminder: {row['remind_minutes']} minutes before and at zero\n"
            f"Repeat: {row['repeat_seconds'] // 3600} hours",
            ephemeral=True,
        )

    @app_commands.command(name="stop", description="Pause this channel's timer/reminders")
    async def stop(self, interaction: discord.Interaction):
        row = get_timer(interaction.channel_id)
        if row is None:
            await interaction.response.send_message("No timer to stop.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            with connect() as db:
                db.execute(
                    "UPDATE countdowns SET active=0,last_render_key='' "
                    "WHERE channel_id=?", (interaction.channel_id,),
                )
            try:
                await interaction.channel.get_partial_message(row["message_id"]).edit(
                    content=None, embeds=[], attachments=[],
                    view=CountdownLayout({**row, "active": 0}),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
        await interaction.followup.send(
            "Timer stopped. Use /countdown post to start a new schedule.",
            ephemeral=True,
        )

    @app_commands.command(name="remove", description="Delete the public timer and saved settings")
    async def remove(self, interaction: discord.Interaction):
        row = get_timer(interaction.channel_id)
        if row is None:
            await interaction.response.send_message("No timer to remove.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            try:
                await interaction.channel.get_partial_message(row["message_id"]).delete()
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException) as exc:
                await interaction.followup.send(
                    f"Could not remove public post ({exc}). Settings untouched.",
                    ephemeral=True,
                )
                return
            with connect() as db:
                db.execute("DELETE FROM countdowns WHERE channel_id=?",
                           (interaction.channel_id,))
        await interaction.followup.send("Timer removed.", ephemeral=True)

    async def _notify_owner(self, row, *, due):
        try:
            owner = self.bot.get_user(row["owner_id"])
            if owner is None:
                owner = await self.bot.fetch_user(row["owner_id"])
            await owner.send(
                ("✅ **Cooldown ready!**" if due else "⏱️ **Countdown reminder**")
                + f"\n**{discord.utils.escape_markdown(row['title'])}**"
                + f"\n{timestamp_label(row['target_ts'])}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            # Mark as attempted; don't retry/log every minute if DMs are off.
            print(f"COUNTDOWN DM FAILED channel={row['channel_id']}: {exc!r}")

    async def _tick_one(self, row, now):
        target = int(row["target_ts"])
        if due_reminder_due(target, now, row["due_notified_target"]):
            # Don't DM a stale reminder after a long downtime.
            if now - target < 3600:
                await self._notify_owner(row, due=True)
            with connect() as db:
                db.execute(
                    "UPDATE countdowns SET due_notified_target=? WHERE channel_id=?",
                    (target, row["channel_id"]),
                )
            row["due_notified_target"] = target

        if row["repeat_seconds"] and now >= target:
            target = next_cycle(target, row["repeat_seconds"], now)
            row["target_ts"] = target
            row["pre_notified_target"] = 0
            row["due_notified_target"] = 0
            with connect() as db:
                db.execute(
                    "UPDATE countdowns SET target_ts=?,pre_notified_target=0,"
                    "due_notified_target=0 WHERE channel_id=?",
                    (target, row["channel_id"]),
                )

        if pre_reminder_due(target, now, row["remind_minutes"], row["pre_notified_target"]):
            await self._notify_owner(row, due=False)
            with connect() as db:
                db.execute(
                    "UPDATE countdowns SET pre_notified_target=? WHERE channel_id=?",
                    (target, row["channel_id"]),
                )

        key = render_key(target, now, row["active"])
        if key == row["last_render_key"]:
            return
        channel = self.bot.get_channel(row["channel_id"])
        if channel is None:
            channel = await self.bot.fetch_channel(row["channel_id"])
        await channel.get_partial_message(row["message_id"]).edit(
            content=None, embeds=[], attachments=[],
            view=CountdownLayout(row, now=now),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        with connect() as db:
            db.execute(
                "UPDATE countdowns SET last_render_key=? WHERE channel_id=?",
                (key, row["channel_id"]),
            )

    async def _tick(self):
        now = int(time.time())
        async with self.lock:
            with connect() as db:
                rows = [dict(row) for row in db.execute(
                    "SELECT * FROM countdowns WHERE active=1"
                )]
            for row in rows:
                try:
                    await self._tick_one(row, now)
                except Exception as exc:
                    print(
                        f"COUNTDOWN UPDATE FAILED channel={row['channel_id']}: {exc!r}"
                    )

    async def _run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"COUNTDOWN LOOP FAILED: {exc!r}")
            await asyncio.sleep(60)


async def setup(bot):
    await bot.add_cog(CountdownCog(bot))
