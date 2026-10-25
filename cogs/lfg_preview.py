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
            level_label TEXT NOT NULL DEFAULT '',
            level_sort INTEGER NOT NULL DEFAULT 1,
            class_name TEXT NOT NULL,
            role TEXT NOT NULL,
            availability TEXT NOT NULL,
            activity TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            avatar_url TEXT NOT NULL DEFAULT '',
            board_day TEXT NOT NULL,
            PRIMARY KEY(channel_id, user_id)
        )""")

        columns = {row[1] for row in db.execute("PRAGMA table_info(lfg_listings)")}
        if "level_label" not in columns:
            db.execute("ALTER TABLE lfg_listings ADD COLUMN level_label TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE lfg_listings SET level_label=CAST(level AS TEXT)")
        if "level_sort" not in columns:
            db.execute("ALTER TABLE lfg_listings ADD COLUMN level_sort INTEGER NOT NULL DEFAULT 1")
            db.execute("UPDATE lfg_listings SET level_sort=level")
        if "avatar_url" not in columns:
            db.execute("ALTER TABLE lfg_listings ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''")


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
            "ORDER BY level_sort DESC, character COLLATE NOCASE ASC",
            (channel_id, today()),
        ).fetchall()


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def board_embeds(channel_id, guild=None):
    roster = entries(channel_id)

    main = discord.Embed(
        title="<Hyped> LFG-LFM Board",
        description=(
            "**Ogre want know plan.**\n"
            "Add what you playing, when you around, and what you want do."
            if roster else
            "**Ogre want know plan.**\nAdd / Update below. Be first on board."
        ),
        color=0x8C5A3C,
    )
    main.set_thumbnail(url="https://i.vgy.me/2Jt4oi.png")
    main.set_footer(text=f"{len(roster)} looking today · Resets each UK day")

    embeds = [main]
    for row in roster[:9]:
        notes = f" · {safe(row['notes'])}" if row["notes"] else ""
        avatar_url = row["avatar_url"]
        if not avatar_url and guild is not None:
            member = guild.get_member(row["user_id"])
            if member is not None:
                avatar_url = str(member.display_avatar.url)

        entry = discord.Embed(
            description=f"{row['availability']} · {safe(row['activity'])}{notes}",
            color=0x8C5A3C,
        )
        author_name = (
            f"Lv {row['level_label'] or row['level']} · {safe(row['character'])} · "
            f"{row['class_name']} · {row['role']}"
        )
        if avatar_url:
            entry.set_author(name=author_name, icon_url=avatar_url)
        else:
            entry.set_author(name=author_name)
        embeds.append(entry)

    if len(roster) > 9:
        main.add_field(
            name="More looking",
            value=f"{len(roster) - 9} more listing(s). Ogre board avatar layout currently shows first 9.",
            inline=False,
        )

    return embeds


def parse_level(value):
    text = value.strip().replace("–", "-").replace("—", "-").replace(" ", "")
    if text.isdecimal():
        number = int(text)
        if 1 <= number <= 60:
            return str(number), number
        return None

    if text.count("-") == 1:
        start_text, end_text = text.split("-", 1)
        if start_text.isdecimal() and end_text.isdecimal():
            start, end = int(start_text), int(end_text)
            if 1 <= start <= end <= 60:
                return f"{start}-{end}", end
    return None


class Draft:
    def __init__(self, row=None):
        self.character = row["character"] if row else ""
        self.level = int(row["level"]) if row else 1
        self.level_label = (row["level_label"] or str(row["level"])) if row else ""
        self.level_sort = int(row["level_sort"]) if row else 1
        self.class_name = row["class_name"] if row else ""
        self.role = row["role"] if row else ""
        self.availability = row["availability"] if row else ""
        self.activity = row["activity"] if row else ""
        self.notes = row["notes"] if row else ""
        self.avatar_url = row["avatar_url"] if row else ""

    def summary(self):
        bits = [
            f"**Character:** {safe(self.character or 'Not set')}",
            f"**Level:** {self.level_label if self.character else 'Not set'}",
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
            label="Level or range (1-60)",
            placeholder="e.g. 36 or 1-12",
            default=state.draft.level_label if state.draft.character else None,
            max_length=5,
        )
        self.add_item(self.character)
        self.add_item(self.level)

    async def on_submit(self, interaction):
        name = self.character.value.strip()
        level_value = parse_level(self.level.value)
        if not name or level_value is None:
            await interaction.response.send_message(
                "Ogre need character name and level like **36** or range like **1-12**.",
                ephemeral=True,
            )
            return

        level_label, level_sort = level_value
        self.state.draft.character = name
        self.state.draft.level = level_sort
        self.state.draft.level_label = level_label
        self.state.draft.level_sort = level_sort
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
                "Ogre missing something. Press Add / Update and run through steps again.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        with connect() as db:
            db.execute(
                """INSERT INTO lfg_listings
                (channel_id,user_id,character,level,level_label,level_sort,class_name,role,
                 availability,activity,notes,avatar_url,board_day)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(channel_id,user_id) DO UPDATE SET
                character=excluded.character,
                level=excluded.level,
                level_label=excluded.level_label,
                level_sort=excluded.level_sort,
                class_name=excluded.class_name,
                role=excluded.role,
                availability=excluded.availability,
                activity=excluded.activity,
                notes=excluded.notes,
                avatar_url=excluded.avatar_url,
                board_day=excluded.board_day""",
                (
                    self.state.channel_id,
                    self.state.owner_id,
                    draft.character,
                    draft.level,
                    draft.level_label,
                    draft.level_sort,
                    draft.class_name,
                    draft.role,
                    draft.availability,
                    draft.activity,
                    draft.notes,
                    str(interaction.user.display_avatar.url),
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
        label="Add / Update",
        style=discord.ButtonStyle.primary,
        custom_id="hyped_lfg_edit_v2",
    )
    async def edit(self, interaction, button):
        state = WizardState(self.cog, self.channel_id, interaction.user.id)
        await interaction.response.send_modal(CharacterLevelModal(state))

    @discord.ui.button(
        label="Remove",
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
                embeds=board_embeds(channel_id, getattr(channel, "guild", None)),
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
                embeds=board_embeds(channel.id, interaction.guild),
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
