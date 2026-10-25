"""Owner-only countdown/cooldown setup with public reminder subscriptions.

One public V2 timer per channel, private owner setup wizard, independent SQLite
storage, optional bottom artwork, and fixed five-minute subscriber reminders.
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
    UK, parse_uk_datetime, upcoming_string, render_key,
    next_cycle, pre_reminder_due, due_reminder_due, validate_image_url,
)

DB_PATH = os.getenv(
    "COUNTDOWN_DB_PATH",
    "/data/countdown.db" if Path("/data").is_dir() else "countdown.db",
)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
ACCENT = 0x8C5A3C
COUNTDOWN_ICON_URL = "https://i.vgy.me/MC2gD0.png"
SUBSCRIBER_LEAD_SECONDS = 5 * 60

OWNER_REMINDER_CHOICES = (
    ("None", 0),
    ("5 minutes", 5),
    ("10 minutes", 10),
    ("15 minutes", 15),
    ("30 minutes", 30),
    ("1 hour", 60),
    ("2 hours", 120),
    ("6 hours", 360),
    ("12 hours", 720),
    ("1 day", 1440),
)
REPEAT_CHOICES = (
    ("One-off", 0),
    ("Every 1 hour", 1),
    ("Every 2 hours", 2),
    ("Every 4 hours", 4),
    ("Every 6 hours", 6),
    ("Every 8 hours", 8),
    ("Every 12 hours", 12),
    ("Every 24 hours", 24),
    ("Every 48 hours", 48),
    ("Every 72 hours", 72),
    ("Every 7 days", 168),
)


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
            description TEXT NOT NULL DEFAULT '',
            target_ts INTEGER NOT NULL,
            image_url TEXT NOT NULL DEFAULT '',
            remind_minutes INTEGER NOT NULL DEFAULT 0,
            repeat_seconds INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            pre_notified_target INTEGER NOT NULL DEFAULT 0,
            due_notified_target INTEGER NOT NULL DEFAULT 0,
            last_render_key TEXT NOT NULL DEFAULT ''
        )""")
        countdown_columns = {
            row[1] for row in db.execute("PRAGMA table_info(countdowns)")
        }
        if "description" not in countdown_columns:
            db.execute(
                "ALTER TABLE countdowns "
                "ADD COLUMN description TEXT NOT NULL DEFAULT ''"
            )
        db.execute("""CREATE TABLE IF NOT EXISTS countdown_subscribers (
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            delivery TEXT NOT NULL DEFAULT 'dm',
            last_notified_target INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(channel_id, user_id)
        )""")
        subscriber_columns = {
            row[1] for row in db.execute(
                "PRAGMA table_info(countdown_subscribers)"
            )
        }
        if "delivery" not in subscriber_columns:
            db.execute(
                "ALTER TABLE countdown_subscribers "
                "ADD COLUMN delivery TEXT NOT NULL DEFAULT 'dm'"
            )
        # PR #30 briefly stored the fixed stopwatch in image_url. That field
        # now means optional bottom artwork only, so remove only that legacy value.
        db.execute(
            "UPDATE countdowns SET image_url='' WHERE image_url=?",
            (COUNTDOWN_ICON_URL,),
        )


def get_timer(channel_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM countdowns WHERE channel_id=?", (channel_id,)
        ).fetchone()
    return dict(row) if row else None


def validate_settings(
    title, description, when_uk, image_url, reminder, repeat_hours, now,
):
    title = " ".join(title.split())
    description = " ".join((description or "").split())
    if not 1 <= len(title) <= 100:
        raise ValueError("Title must be 1 to 100 characters.")
    if len(description) > 180:
        raise ValueError("Description must be 180 characters or fewer.")
    target = parse_uk_datetime(when_uk)
    if target <= now:
        raise ValueError("Choose a future UK date and time.")
    if not 0 <= reminder <= 10080:
        raise ValueError("Reminder must be 0 to 10080 minutes.")
    if not 0 <= repeat_hours <= 8760:
        raise ValueError("Repeat must be 0 to 8760 hours.")
    return dict(
        title=title,
        description=description,
        target_ts=target,
        image_url=validate_image_url(image_url),
        remind_minutes=reminder,
        repeat_seconds=repeat_hours * 3600,
        active=1,
    )


def timestamp_label(target):
    return f"<t:{target}:F> · <t:{target}:R>"


def setup_target_string(state):
    return (
        f"{state.date_value.isoformat()} "
        f"{int(state.hour):02d}:{int(state.minute):02d}"
    )


class ReminderButton(discord.ui.Button):
    """Toggle one five-minute delivery method for this countdown."""

    def __init__(self, channel_id, mode, *, disabled=False):
        if mode not in ("dm", "ping"):
            raise ValueError("Reminder mode must be dm or ping")
        self.channel_id = channel_id
        self.mode = mode
        super().__init__(
            label="DM" if mode == "dm" else "Ping",
            emoji="🔔",
            style=discord.ButtonStyle.secondary,
            custom_id=f"hyped_countdown_remind_{mode}_v1",
            disabled=disabled,
        )

    async def callback(self, interaction):
        timer = get_timer(self.channel_id)
        if timer is None or not timer["active"]:
            await interaction.response.send_message(
                "Ogre find no active timer here.", ephemeral=True,
            )
            return

        user_id = interaction.user.id
        with connect() as db:
            row = db.execute(
                "SELECT delivery FROM countdown_subscribers "
                "WHERE channel_id=? AND user_id=?",
                (self.channel_id, user_id),
            ).fetchone()

            if row and row["delivery"] == self.mode:
                db.execute(
                    "DELETE FROM countdown_subscribers "
                    "WHERE channel_id=? AND user_id=?",
                    (self.channel_id, user_id),
                )
                subscribed = False
                switched = False
            else:
                db.execute(
                    """INSERT INTO countdown_subscribers
                       (channel_id,user_id,delivery,last_notified_target)
                       VALUES (?,?,?,0)
                       ON CONFLICT(channel_id,user_id) DO UPDATE SET
                           delivery=excluded.delivery,
                           last_notified_target=0""",
                    (self.channel_id, user_id, self.mode),
                )
                subscribed = True
                switched = row is not None

        if not subscribed:
            text = "🔕 **Ogre forget reminder.** No five-minute bonk for this one."
        elif self.mode == "dm":
            prefix = "🔔 **Ogre switch reminder.**" if switched else "🔔 **Ogre remember!**"
            text = (
                f"{prefix} Ogre bonk your **DMs** 5 minutes before "
                f"**{discord.utils.escape_markdown(timer['title'])}**."
            )
        else:
            prefix = "🔔 **Ogre switch reminder.**" if switched else "🔔 **Ogre remember!**"
            text = (
                f"{prefix} Ogre **ping you here** 5 minutes before "
                f"**{discord.utils.escape_markdown(timer['title'])}**, "
                "then Ogre clean message away."
            )
        await interaction.response.send_message(text, ephemeral=True)


class CountdownLayout(discord.ui.LayoutView):
    """Compact event card with fixed right icon and optional bottom artwork."""

    def __init__(
        self, config, *, now=None, preview=False, channel_id=None,
        include_remind=True,
    ):
        super().__init__(timeout=900 if preview else None)
        now = int(time.time()) if now is None else now
        target = int(config["target_ts"])
        repeat = int(config["repeat_seconds"])
        active = bool(config["active"])

        if not active:
            remaining = "Paused"
        elif now >= target and not repeat:
            remaining = "Ready!"
        else:
            remaining = upcoming_string(target, now)

        title = discord.utils.escape_markdown(config["title"])
        description = discord.utils.escape_markdown(
            config.get("description", "")
        )
        subtitle = f"<t:{target}:F>"
        if preview:
            subtitle += " · DEMO PREVIEW"

        header = discord.ui.Section(
            discord.ui.TextDisplay(
                f"## {title}\n"
                + (f"{description}\n" if description else "")
                + f"-# {subtitle}\n"
                + "\n"
                + f"**{remaining}**"
            ),
            accessory=discord.ui.Thumbnail(
                COUNTDOWN_ICON_URL,
                description="Hyped stopwatch icon",
            ),
        )
        panel = discord.ui.Container(header, accent_color=ACCENT)
        if repeat:
            panel.add_item(discord.ui.TextDisplay(
                f"-# Repeats every {repeat // 3600}h"
            ))
        self.add_item(panel)

        if include_remind:
            disabled = preview or not active
            self.add_item(discord.ui.ActionRow(
                ReminderButton(channel_id or 0, "dm", disabled=disabled),
                ReminderButton(channel_id or 0, "ping", disabled=disabled),
            ))

        # Optional owner-supplied artwork is always the very bottom component.
        image_url = config.get("image_url", "")
        if image_url:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(
                media=image_url,
                description=f"{config['title']} artwork",
            )
            self.add_item(gallery)


class SetupState:
    def __init__(self, cog, interaction):
        self.cog = cog
        self.owner_id = interaction.user.id
        self.channel_id = interaction.channel_id
        self.guild_id = interaction.guild_id
        self.guild = interaction.guild
        now = datetime.now(UK) + timedelta(hours=2)
        self.title = "Hyped Countdown"
        self.description = ""
        self.image_url = ""
        self.date_value = now.date()
        self.hour = now.hour
        self.minute = (now.minute // 5 * 5) % 60
        self.remind_minutes = 30
        self.repeat_hours = 0
        self.has_existing = False

    async def check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This setup belongs to another Ogre wrangler.", ephemeral=True,
            )
            return False
        return True

    def config(self):
        return validate_settings(
            self.title,
            self.description,
            setup_target_string(self),
            self.image_url,
            self.remind_minutes,
            self.repeat_hours,
            int(time.time()),
        )


class SetupDetailsModal(discord.ui.Modal, title="Countdown details"):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.event_title = discord.ui.TextInput(
            label="Event title",
            placeholder="e.g. WoW: Forever Launch",
            default=state.title,
            max_length=100,
        )
        self.description = discord.ui.TextInput(
            label="Subtitle / description (optional)",
            placeholder="e.g. Reserve your character name before launch",
            default=state.description or None,
            required=False,
            max_length=180,
        )
        self.artwork = discord.ui.TextInput(
            label="Bottom image URL (optional)",
            placeholder="https://i.vgy.me/....png",
            default=state.image_url or None,
            required=False,
            max_length=1024,
        )
        self.add_item(self.event_title)
        self.add_item(self.description)
        self.add_item(self.artwork)

    async def on_submit(self, interaction):
        if not await self.state.check(interaction):
            return
        title = " ".join(self.event_title.value.split())
        if not title:
            await interaction.response.send_message(
                "Give Ogre an event title.", ephemeral=True,
            )
            return
        try:
            image = validate_image_url(self.artwork.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.state.title = title
        self.state.description = " ".join(self.description.value.split())
        self.state.image_url = image
        await interaction.response.send_message(
            content="Choose the UK date and time.",
            view=SetupDateTimeView(self.state),
            ephemeral=True,
        )


class SetupDateSelect(discord.ui.Select):
    def __init__(self, state):
        today = datetime.now(UK).date()
        options = []
        for offset in range(25):
            day = today + timedelta(days=offset)
            label = day.strftime("%a %-d %b")
            if offset == 0:
                label += " · Today"
            elif offset == 1:
                label += " · Tomorrow"
            options.append(discord.SelectOption(
                label=label,
                value=day.isoformat(),
                default=day == state.date_value,
            ))
        super().__init__(
            placeholder="Date",
            min_values=1, max_values=1, options=options, row=0,
        )

    async def callback(self, interaction):
        self.view.state.date_value = datetime.strptime(
            self.values[0], "%Y-%m-%d"
        ).date()
        await interaction.response.edit_message(
            content="Choose the UK date and time.",
            view=SetupDateTimeView(self.view.state),
        )


class SetupHourSelect(discord.ui.Select):
    def __init__(self, state):
        options = [
            discord.SelectOption(
                label=f"{hour:02d}:00",
                value=str(hour),
                default=hour == state.hour,
            )
            for hour in range(24)
        ]
        super().__init__(
            placeholder="Hour (UK)", min_values=1, max_values=1,
            options=options, row=1,
        )

    async def callback(self, interaction):
        self.view.state.hour = int(self.values[0])
        await interaction.response.edit_message(
            content="Choose the UK date and time.",
            view=SetupDateTimeView(self.view.state),
        )


class SetupMinuteSelect(discord.ui.Select):
    def __init__(self, state):
        options = [
            discord.SelectOption(
                label=f":{minute:02d}",
                value=str(minute),
                default=minute == state.minute,
            )
            for minute in range(0, 60, 5)
        ]
        super().__init__(
            placeholder="Minute", min_values=1, max_values=1,
            options=options, row=2,
        )

    async def callback(self, interaction):
        self.view.state.minute = int(self.values[0])
        await interaction.response.edit_message(
            content="Choose the UK date and time.",
            view=SetupDateTimeView(self.view.state),
        )


class SetupDateTimeView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(SetupDateSelect(state))
        self.add_item(SetupHourSelect(state))
        self.add_item(SetupMinuteSelect(state))

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)

    @discord.ui.button(label="‹ Details", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction, button):
        await interaction.response.send_modal(SetupDetailsModal(self.state))

    @discord.ui.button(label="Next ›", style=discord.ButtonStyle.primary, row=3)
    async def next(self, interaction, button):
        try:
            parse_uk_datetime(setup_target_string(self.state))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if parse_uk_datetime(setup_target_string(self.state)) <= int(time.time()):
            await interaction.response.send_message(
                "That time is already gone. Ogre need future time.", ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="Choose reminder and repeat settings.",
            view=SetupOptionsView(self.state),
        )


class SetupReminderSelect(discord.ui.Select):
    def __init__(self, state):
        super().__init__(
            placeholder="Owner reminder",
            min_values=1, max_values=1, row=0,
            options=[
                discord.SelectOption(
                    label=label, value=str(value),
                    default=value == state.remind_minutes,
                )
                for label, value in OWNER_REMINDER_CHOICES
            ],
        )

    async def callback(self, interaction):
        self.view.state.remind_minutes = int(self.values[0])
        await interaction.response.edit_message(
            content="Choose reminder and repeat settings.",
            view=SetupOptionsView(self.view.state),
        )


class SetupRepeatSelect(discord.ui.Select):
    def __init__(self, state):
        super().__init__(
            placeholder="Repeat / cooldown",
            min_values=1, max_values=1, row=1,
            options=[
                discord.SelectOption(
                    label=label, value=str(value),
                    default=value == state.repeat_hours,
                )
                for label, value in REPEAT_CHOICES
            ],
        )

    async def callback(self, interaction):
        self.view.state.repeat_hours = int(self.values[0])
        await interaction.response.edit_message(
            content="Choose reminder and repeat settings.",
            view=SetupOptionsView(self.view.state),
        )


class SetupOptionsView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(SetupReminderSelect(state))
        self.add_item(SetupRepeatSelect(state))

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)

    @discord.ui.button(label="‹ Date / Time", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction, button):
        await interaction.response.edit_message(
            content="Choose the UK date and time.",
            view=SetupDateTimeView(self.state),
        )

    @discord.ui.button(label="Preview ›", style=discord.ButtonStyle.primary, row=2)
    async def preview(self, interaction, button):
        try:
            config = self.state.config()
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=None,
            view=SetupReviewLayout(self.state, config),
        )


class SetupReviewLayout(CountdownLayout):
    def __init__(self, state, config):
        super().__init__(
            config, preview=True, channel_id=state.channel_id, include_remind=True,
        )
        self.state = state
        self.add_item(discord.ui.ActionRow(
            SetupReviewBackButton(state),
            SetupPublishButton(state),
        ))
        if state.has_existing:
            self.add_item(discord.ui.ActionRow(
                SetupTestDMButton(state),
                SetupStopButton(state),
                SetupRemoveButton(state),
            ))


class SetupReviewBackButton(discord.ui.Button):
    def __init__(self, state):
        super().__init__(
            label="‹ Settings", style=discord.ButtonStyle.secondary,
        )
        self.state = state

    async def callback(self, interaction):
        if not await self.state.check(interaction):
            return
        await interaction.response.edit_message(
            content="Choose reminder and repeat settings.",
            view=SetupOptionsView(self.state),
        )


class SetupPublishButton(discord.ui.Button):
    def __init__(self, state):
        super().__init__(label="Publish", style=discord.ButtonStyle.success)
        self.state = state

    async def callback(self, interaction):
        if not await self.state.check(interaction):
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use setup in the public text channel where timer should live.",
                ephemeral=True,
            )
            return
        try:
            config = self.state.config()
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            message = await self.state.cog.publish_config(
                channel, self.state.guild_id, self.state.owner_id, config,
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"COUNTDOWN SETUP PUBLISH FAILED channel={channel.id}: {exc!r}")
            await interaction.edit_original_response(
                content="Ogre cannot publish. Check Send Messages / Embed Links.",
                view=None,
            )
            return
        await interaction.edit_original_response(
            content=f"✅ Ogre post timer: {message.jump_url}",
            view=None,
        )


class SetupTestDMButton(discord.ui.Button):
    def __init__(self, state):
        super().__init__(label="Test DM", style=discord.ButtonStyle.secondary)
        self.state = state

    async def callback(self, interaction):
        if not await self.state.check(interaction):
            return
        row = get_timer(self.state.channel_id)
        title = row["title"] if row else self.state.title
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await interaction.user.send(
                "⏱️ **Ogre bonk clock! Five minutes!**\n"
                f"**{discord.utils.escape_markdown(title)}** "
                "almost here. Get ready, gamer.\n"
                "-# Dev test from Hyped Ogre",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "❌ Ogre could not DM you. Check Discord privacy settings.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "✅ Ogre sent test DM.", ephemeral=True,
        )


class SetupStopButton(discord.ui.Button):
    def __init__(self, state):
        super().__init__(label="Stop", style=discord.ButtonStyle.secondary)
        self.state = state

    async def callback(self, interaction):
        if not await self.state.check(interaction):
            return
        row = get_timer(self.state.channel_id)
        if row is None:
            await interaction.response.send_message(
                "No saved timer here.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        with connect() as db:
            db.execute(
                "UPDATE countdowns SET active=0,last_render_key='' "
                "WHERE channel_id=?",
                (self.state.channel_id,),
            )
        try:
            await interaction.channel.get_partial_message(row["message_id"]).edit(
                content=None, embeds=[], attachments=[],
                view=CountdownLayout(
                    {**row, "active": 0},
                    channel_id=self.state.channel_id,
                    include_remind=True,
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        await interaction.followup.send("Timer stopped.", ephemeral=True)


class SetupRemoveButton(discord.ui.Button):
    def __init__(self, state):
        super().__init__(label="Remove", style=discord.ButtonStyle.danger)
        self.state = state

    async def callback(self, interaction):
        if not await self.state.check(interaction):
            return
        row = get_timer(self.state.channel_id)
        if row is None:
            await interaction.response.send_message(
                "No saved timer here.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await interaction.channel.get_partial_message(row["message_id"]).delete()
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Ogre could not delete the public timer. Saved timer kept.",
                ephemeral=True,
            )
            return
        with connect() as db:
            db.execute(
                "DELETE FROM countdown_subscribers WHERE channel_id=?",
                (self.state.channel_id,),
            )
            db.execute(
                "DELETE FROM countdowns WHERE channel_id=?",
                (self.state.channel_id,),
            )
        self.state.has_existing = False
        await interaction.followup.send("Timer removed.", ephemeral=True)


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class CountdownCog(
    commands.GroupCog, group_name="countdown",
    group_description="Owner-only countdown setup",
):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.task = None

    async def cog_load(self):
        init_db()
        # Re-register persistent Remind buttons after restarts.
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM countdowns"
            )]
        for row in rows:
            self.bot.add_view(
                CountdownLayout(
                    row, channel_id=row["channel_id"], include_remind=True,
                ),
                message_id=row["message_id"],
            )
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

    @app_commands.command(name="setup", description="Set up a timer with friendly dropdowns")
    async def setup_timer(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use this in the channel where the public timer should live.",
                ephemeral=True,
            )
            return
        state = SetupState(self, interaction)
        existing = get_timer(interaction.channel_id)
        if existing:
            state.has_existing = True
            state.title = existing["title"]
            state.description = existing.get("description", "")
            state.image_url = existing["image_url"]
            state.remind_minutes = existing["remind_minutes"]
            state.repeat_hours = existing["repeat_seconds"] // 3600
            local = datetime.fromtimestamp(existing["target_ts"], UK)
            state.date_value = local.date()
            state.hour = local.hour
            state.minute = local.minute - (local.minute % 5)
        await interaction.response.send_modal(SetupDetailsModal(state))

    async def publish_config(self, channel, guild_id, owner_id, config):
        async with self.lock:
            existing = get_timer(channel.id)
            message = None
            if existing:
                try:
                    message = await channel.fetch_message(existing["message_id"])
                except discord.NotFound:
                    pass

            view = CountdownLayout(
                config, channel_id=channel.id, include_remind=True,
            )
            if message is not None:
                await message.edit(
                    content=None, embeds=[], attachments=[], view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                message = await channel.send(
                    view=view, allowed_mentions=discord.AllowedMentions.none(),
                )

            with connect() as db:
                db.execute(
                    """INSERT INTO countdowns (
                        channel_id, guild_id, message_id, owner_id, title,
                        description, target_ts, image_url, remind_minutes, repeat_seconds,
                        active, pre_notified_target, due_notified_target,
                        last_render_key
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,1,0,0,?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        guild_id=excluded.guild_id,
                        message_id=excluded.message_id,
                        owner_id=excluded.owner_id,
                        title=excluded.title,
                        description=excluded.description,
                        target_ts=excluded.target_ts,
                        image_url=excluded.image_url,
                        remind_minutes=excluded.remind_minutes,
                        repeat_seconds=excluded.repeat_seconds,
                        active=1,
                        pre_notified_target=0,
                        due_notified_target=0,
                        last_render_key=excluded.last_render_key""",
                    (
                        channel.id, guild_id, message.id, owner_id,
                        config["title"], config["description"],
                        config["target_ts"], config["image_url"],
                        config["remind_minutes"], config["repeat_seconds"],
                        render_key(config["target_ts"], int(time.time()), 1),
                    ),
                )
            return message

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
            print(f"COUNTDOWN OWNER DM FAILED channel={row['channel_id']}: {exc!r}")

    async def _notify_subscribers(self, row, now):
        target = int(row["target_ts"])
        if not (0 < target - now <= SUBSCRIBER_LEAD_SECONDS):
            return
        with connect() as db:
            subscribers = [dict(item) for item in db.execute(
                "SELECT user_id,delivery,last_notified_target "
                "FROM countdown_subscribers "
                "WHERE channel_id=? AND last_notified_target<>?",
                (row["channel_id"], target),
            )]

        channel = None
        for subscriber in subscribers:
            try:
                user = self.bot.get_user(subscriber["user_id"])
                if user is None:
                    user = await self.bot.fetch_user(subscriber["user_id"])

                if subscriber["delivery"] == "ping":
                    if channel is None:
                        channel = self.bot.get_channel(row["channel_id"])
                        if channel is None:
                            channel = await self.bot.fetch_channel(row["channel_id"])
                    await channel.send(
                        f"<@{subscriber['user_id']}> ⏱️ **Ogre bonk clock! "
                        "Five minutes!**\n"
                        f"**{discord.utils.escape_markdown(row['title'])}** "
                        "almost here. Get ready, gamer.",
                        allowed_mentions=discord.AllowedMentions(
                            users=True, roles=False, everyone=False,
                        ),
                        delete_after=60,
                    )
                else:
                    await user.send(
                        "⏱️ **Ogre bonk clock! Five minutes!**\n"
                        f"**{discord.utils.escape_markdown(row['title'])}** "
                        "almost here. Get ready, gamer.\n"
                        f"{timestamp_label(target)}",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(
                    f"COUNTDOWN SUBSCRIBER {subscriber['delivery'].upper()} FAILED "
                    f"channel={row['channel_id']} "
                    f"user={subscriber['user_id']}: {exc!r}"
                )
            finally:
                # One delivery attempt per user per target; no minute spam.
                with connect() as db:
                    db.execute(
                        "UPDATE countdown_subscribers "
                        "SET last_notified_target=? "
                        "WHERE channel_id=? AND user_id=?",
                        (target, row["channel_id"], subscriber["user_id"]),
                    )

    async def _tick_one(self, row, now):
        target = int(row["target_ts"])

        # Subscriber reminder comes before cycle rollover and only in final 5m.
        await self._notify_subscribers(row, now)

        if due_reminder_due(target, now, row["due_notified_target"]):
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

        if pre_reminder_due(
            target, now, row["remind_minutes"], row["pre_notified_target"]
        ):
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
            view=CountdownLayout(
                row, now=now, channel_id=row["channel_id"], include_remind=True,
            ),
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
