"""Hyped guild LFG board, piloted through /lfg-preview.

The board is live in the chosen test channel, but stored in its own SQLite file.
Existing dice, Flameboard and Forever-role systems are untouched.
"""
import asyncio
from datetime import datetime
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

TZ = ZoneInfo("Europe/London")
PATH = os.getenv("LFG_DB_PATH", "/data/lfg_board.db" if Path("/data").is_dir() else "lfg_board.db")

CLASSES = ("Warrior", "Paladin", "Hunter", "Rogue", "Priest", "Shaman", "Mage", "Warlock", "Druid")
ROLES = ("Tank", "Healer", "DPS", "Flexible")
WHEN = ("On now", "All day", "After work", "Evening", "Late night", "Flexible")
WHAT = ("Dungeons", "Group questing", "Levelling", "PvP", "Farming", "Anything")


def today():
    return datetime.now(TZ).date().isoformat()


def connect():
    db = sqlite3.connect(PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def init_db():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS lfg_boards (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            board_day TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS lfg_listings (
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            character TEXT NOT NULL,
            level INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            role TEXT NOT NULL,
            availability TEXT NOT NULL,
            activity TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            board_day TEXT NOT NULL,
            PRIMARY KEY(channel_id, user_id)
        )""")


def get_board(channel_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM lfg_boards WHERE channel_id=?",
            (channel_id,),
        ).fetchone()


def current_listing(channel_id, user_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM lfg_listings WHERE channel_id=? AND user_id=? AND board_day=?",
            (channel_id, user_id, today()),
        ).fetchone()


def entries(channel_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM lfg_listings WHERE channel_id=? AND board_day=? "
            "ORDER BY level DESC, character COLLATE NOCASE ASC",
            (channel_id, today()),
        ).fetchall()


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def board_embed(channel_id):
    roster = entries(channel_id)
    lines = []
    for row in roster:
        activity = safe(row["activity"])
        notes = f" · {safe(row['notes'])}" if row["notes"] else ""
        lines.append(
            f"**Lv {row['level']} · {safe(row['character'])} · {row['class_name']} · {row['role']}**\n"
            f"{row['availability']} · {activity}{notes}"
        )

    embed = discord.Embed(
        title="HYPED | LFG-LFM",
        description=(
            "Who's around, when are they playing, and what are they looking for?\n"
            "Add yourself or update your details below. This is a noticeboard, not a signup.\n\n"
            + ("\n\n".join(lines) if lines else "*Nobody listed yet. Be the first!*")
        )[:4000],
        color=discord.Color.from_rgb(217, 154, 67),
    )
    embed.set_footer(text=f"{len(roster)} adventurer(s) · {today()} · Resets each UK day")
    return embed


class Draft:
    def __init__(self, row=None):
        self.character = row["character"] if row else ""
        self.level = int(row["level"]) if row else 1
        self.class_name = row["class_name"] if row else ""
        self.role = row["role"] if row else ""
        self.availability = row["availability"] if row else ""
        self.activity = row["activity"] if row else ""
        self.notes = row["notes"] if row else ""

    def summary(self):
        bits = [
            f"**Character:** {safe(self.character or 'Not set')}",
            f"**Level:** {self.level if self.character else 'Not set'}",
            f"**Class:** {self.class_name or 'Not set'}",
            f"**Role:** {self.role or 'Not set'}",
            f"**Availability:** {self.availability or 'Not set'}",
            f"**Looking for:** {safe(self.activity or 'Not set')}",
        ]
        if self.notes:
            bits.append(f"**Notes:** {safe(self.notes)}")
        return "\n".join(bits)


class WizardState:
    def __init__(self, cog, channel_id, owner_id):
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.draft = Draft(current_listing(channel_id, owner_id))

    async def check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This Ogre conversation belongs to someone else.",
                ephemeral=True,
            )
            return False
        return True


def step_embed(step, title, prompt, state):
    embed = discord.Embed(
        title=f"Hyped Ogre LFG · Step {step}",
        description=f"{prompt}\n\n{state.draft.summary()}",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Only you can see this. Nothing changes until you save.")
    return embed


class CharacterLevelModal(discord.ui.Modal, title="Tell Ogre who you're playing"):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.character = discord.ui.TextInput(
            label="Character name",
            placeholder="e.g. Kami",
            default=state.draft.character or None,
            max_length=32,
        )
        self.level = discord.ui.TextInput(
            label="Level (1-60)",
            placeholder="e.g. 36",
            default=str(state.draft.level) if state.draft.character else None,
            max_length=2,
        )
        self.add_item(self.character)
        self.add_item(self.level)

    async def on_submit(self, interaction):
        name = self.character.value.strip()
        level_text = self.level.value.strip()
        if not name or not level_text.isdecimal() or not (1 <= int(level_text) <= 60):
            await interaction.response.send_message(
                "Ogre needs a character name and a whole-number level from 1 to 60.",
                ephemeral=True,
            )
            return

        self.state.draft.character = name
        self.state.draft.level = int(level_text)
        await interaction.response.send_message(
            embed=step_embed(
                "2/5",
                "Pick your class",
                "Good. What class are you playing?",
                self.state,
            ),
            view=ClassView(self.state),
            ephemeral=True,
        )


class ClassSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose your class",
            options=[discord.SelectOption(label=value, value=value) for value in CLASSES],
        )

    async def callback(self, interaction):
        state = self.view.state
        state.draft.class_name = self.values[0]
        await interaction.response.edit_message(
            embed=step_embed(
                "3/5",
                "Choose your role",
                "What job are you looking to fill?",
                state,
            ),
            view=RoleView(state),
        )


class ClassView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(ClassSelect())

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class RoleButton(discord.ui.Button):
    def __init__(self, value):
        super().__init__(
            label=value,
            style=discord.ButtonStyle.primary if value != "Flexible" else discord.ButtonStyle.secondary,
        )
        self.value = value

    async def callback(self, interaction):
        state = self.view.state
        state.draft.role = self.value
        await interaction.response.edit_message(
            embed=step_embed(
                "4/5",
                "When are you around?",
                "Pick the availability that best describes today.",
                state,
            ),
            view=AvailabilityView(state),
        )


class RoleView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        for value in ROLES:
            self.add_item(RoleButton(value))

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class AvailabilityButton(discord.ui.Button):
    def __init__(self, value, row):
        super().__init__(
            label=value,
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.value = value

    async def callback(self, interaction):
        state = self.view.state
        state.draft.availability = self.value
        await interaction.response.edit_message(
            embed=step_embed(
                "5/5",
                "What are you looking for?",
                "Choose up to three things. You'll review everything before Ogre posts it.",
                state,
            ),
            view=ActivityView(state),
        )


class AvailabilityView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        for index, value in enumerate(WHEN):
            self.add_item(AvailabilityButton(value, 0 if index < 5 else 1))

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class ActivitySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose up to 3 activities",
            min_values=1,
            max_values=3,
            options=[discord.SelectOption(label=value, value=value) for value in WHAT],
        )

    async def callback(self, interaction):
        state = self.view.state
        state.draft.activity = ", ".join(self.values)
        await interaction.response.edit_message(
            embed=review_embed(state),
            view=ReviewView(state),
        )


class ActivityView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(ActivitySelect())

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


def review_embed(state):
    embed = discord.Embed(
        title="Hyped Ogre LFG · Review",
        description=(
            "Ogre thinks this is what you mean. Add an optional note or save it to the board.\n\n"
            + state.draft.summary()
        ),
        color=discord.Color.from_rgb(217, 154, 67),
    )
    embed.set_footer(text="Only you can see this review")
    return embed


class NoteModal(discord.ui.Modal, title="Optional LFG note"):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.note = discord.ui.TextInput(
            label="Dungeon / quest / extra note",
            placeholder="e.g. SM Cath, happy to tank",
            default=state.draft.notes or None,
            required=False,
            max_length=80,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction):
        self.state.draft.notes = self.note.value.strip()
        await interaction.response.edit_message(
            embed=review_embed(self.state),
            view=ReviewView(self.state),
        )


class ReviewView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)

    @discord.ui.button(label="Save Listing", style=discord.ButtonStyle.success)
    async def save(self, interaction, button):
        draft = self.state.draft
        required = (
            draft.character,
            draft.class_name,
            draft.role,
            draft.availability,
            draft.activity,
        )
        if not all(required):
            await interaction.response.send_message(
                "Ogre is missing something. Press Add / Update Me and run through the steps again.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        with connect() as db:
            db.execute(
                """INSERT INTO lfg_listings
                (channel_id,user_id,character,level,class_name,role,availability,activity,notes,board_day)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(channel_id,user_id) DO UPDATE SET
                character=excluded.character,
                level=excluded.level,
                class_name=excluded.class_name,
                role=excluded.role,
                availability=excluded.availability,
                activity=excluded.activity,
                notes=excluded.notes,
                board_day=excluded.board_day""",
                (
                    self.state.channel_id,
                    self.state.owner_id,
                    draft.character,
                    draft.level,
                    draft.class_name,
                    draft.role,
                    draft.availability,
                    draft.activity,
                    draft.notes,
                    today(),
                ),
            )

        try:
            await self.state.cog.refresh(self.state.channel_id)
            message = "Saved. Ogre has updated the board."
        except Exception as exc:
            print(
                f"LFG BOARD REFRESH FAILED channel={self.state.channel_id} "
                f"user={self.state.owner_id} error={exc!r}"
            )
            message = (
                "Your listing was saved, but Ogre could not refresh the board. "
                "Tell an officer so we can check the bot log."
            )

        await interaction.edit_original_response(
            content=message,
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Add Note", style=discord.ButtonStyle.secondary)
    async def note(self, interaction, button):
        await interaction.response.send_modal(NoteModal(self.state))

    @discord.ui.button(label="Start Again", style=discord.ButtonStyle.secondary)
    async def restart(self, interaction, button):
        await interaction.response.send_modal(CharacterLevelModal(self.state))


class BoardView(discord.ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id

    @discord.ui.button(
        label="Add / Update Me",
        style=discord.ButtonStyle.primary,
        custom_id="hyped_lfg_edit_v2",
    )
    async def edit(self, interaction, button):
        state = WizardState(self.cog, self.channel_id, interaction.user.id)
        await interaction.response.send_modal(CharacterLevelModal(state))

    @discord.ui.button(
        label="Remove Me",
        style=discord.ButtonStyle.secondary,
        custom_id="hyped_lfg_remove_v2",
    )
    async def remove(self, interaction, button):
        with connect() as db:
            result = db.execute(
                "DELETE FROM lfg_listings WHERE channel_id=? AND user_id=?",
                (self.channel_id, interaction.user.id),
            )

        try:
            await self.cog.refresh(self.channel_id)
            refreshed = True
        except Exception as exc:
            refreshed = False
            print(
                f"LFG BOARD REFRESH FAILED channel={self.channel_id} "
                f"user={interaction.user.id} error={exc!r}"
            )

        if result.rowcount and refreshed:
            text = "Ogre removed you from the board."
        elif result.rowcount:
            text = "You were removed, but Ogre could not refresh the board display."
        else:
            text = "You don't currently have a listing on this board."

        await interaction.response.send_message(text, ephemeral=True)


class LFGPreview(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    async def cog_load(self):
        init_db()
        with connect() as db:
            boards = db.execute(
                "SELECT channel_id, message_id FROM lfg_boards"
            ).fetchall()

        for board in boards:
            self.bot.add_view(
                BoardView(self, board["channel_id"]),
                message_id=board["message_id"],
            )

        self.daily_refresh.start()

    async def cog_unload(self):
        self.daily_refresh.cancel()

    async def refresh(self, channel_id):
        async with self.lock:
            row = get_board(channel_id)
            if not row:
                raise RuntimeError("No stored LFG board for this channel")

            channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)

            message = channel.get_partial_message(row["message_id"])
            await message.edit(
                embed=board_embed(channel_id),
                view=BoardView(self, channel_id),
            )
            return True

    @tasks.loop(minutes=1)
    async def daily_refresh(self):
        with connect() as db:
            rows = db.execute(
                "SELECT channel_id FROM lfg_boards WHERE board_day != ?",
                (today(),),
            ).fetchall()
            if rows:
                db.execute(
                    "DELETE FROM lfg_listings WHERE board_day != ?",
                    (today(),),
                )
                db.execute(
                    "UPDATE lfg_boards SET board_day=?",
                    (today(),),
                )

        for row in rows:
            try:
                await self.refresh(row["channel_id"])
            except Exception as exc:
                print(
                    f"LFG DAILY REFRESH FAILED channel={row['channel_id']} "
                    f"error={exc!r}"
                )

    @daily_refresh.before_loop
    async def ready_for_refresh(self):
        await self.bot.wait_until_ready()

    async def post_new_board(self, interaction, channel):
        try:
            await interaction.response.send_message(
                embed=board_embed(channel.id),
                view=BoardView(self, channel.id),
            )
            msg = await interaction.original_response()
            with connect() as db:
                db.execute(
                    "INSERT INTO lfg_boards(channel_id,guild_id,message_id,board_day) "
                    "VALUES(?,?,?,?) "
                    "ON CONFLICT(channel_id) DO UPDATE SET "
                    "message_id=excluded.message_id,"
                    "guild_id=excluded.guild_id,"
                    "board_day=excluded.board_day",
                    (channel.id, interaction.guild_id, msg.id, today()),
                )
        except Exception as exc:
            print(f"LFG BOARD POST FAILED channel={channel.id} error={exc!r}")
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Ogre couldn't finish setting up the board. Check the Fly log for the exact error.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Ogre couldn't post the board. Check the Fly log for the exact error.",
                    ephemeral=True,
                )

    @app_commands.command(
        name="lfg-preview",
        description="Create or reopen the interactive Hyped LFG test board",
    )
    @app_commands.guilds(discord.Object(id=int(os.environ["HYPED_GUILD_ID"])))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def lfg_preview(self, interaction: discord.Interaction):
        if interaction.guild_id != int(os.environ["HYPED_GUILD_ID"]):
            await interaction.response.send_message(
                "Only available in Hyped.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use this in a server text channel.",
                ephemeral=True,
            )
            return

        row = get_board(channel.id)
        if row:
            try:
                board_message = await channel.fetch_message(row["message_id"])
                await self.refresh(channel.id)
                await interaction.response.send_message(
                    f"Ogre found the existing board: {board_message.jump_url}",
                    ephemeral=True,
                )
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(
                    f"LFG EXISTING BOARD INVALID channel={channel.id} "
                    f"message={row['message_id']} error={exc!r}"
                )

        await self.post_new_board(interaction, channel)


async def setup(bot):
    await bot.add_cog(LFGPreview(bot))
