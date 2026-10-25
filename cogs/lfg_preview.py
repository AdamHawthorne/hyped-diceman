"""Hyped guild LFG board, piloted through /lfg-preview.

Separate SQLite file and isolated Cog. No touch to dice/Flameboard/role storage.
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
            channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL, board_day TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS lfg_listings (
            channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            character TEXT NOT NULL, level INTEGER NOT NULL,
            class_name TEXT NOT NULL, role TEXT NOT NULL,
            availability TEXT NOT NULL, activity TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '', board_day TEXT NOT NULL,
            PRIMARY KEY(channel_id, user_id)
        )""")


def get_board(channel_id):
    with connect() as db:
        return db.execute("SELECT * FROM lfg_boards WHERE channel_id=?", (channel_id,)).fetchone()


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
            "ORDER BY level DESC, character COLLATE NOCASE ASC", (channel_id, today())
        ).fetchall()


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def board_embed(channel_id):
    roster = entries(channel_id)
    lines = [
        f"**Lv {r['level']} · {safe(r['character'])} · {r['class_name']} · {r['role']}**\n"
        f"{r['availability']} · {safe(r['activity'])}"
        + (f" · {safe(r['notes'])}" if r["notes"] else "")
        for r in roster
    ]
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
        self.level = row["level"] if row else 1
        self.class_name = row["class_name"] if row else "Warrior"
        self.role = row["role"] if row else "DPS"
        self.availability = row["availability"] if row else "Evening"
        self.activity = row["activity"] if row else "Dungeons"
        self.notes = row["notes"] if row else ""

    def describe(self):
        return (
            f"**Lv {self.level} · {safe(self.character or 'Character name needed')} · "
            f"{self.class_name} · {self.role}**\n"
            f"{self.availability} · {safe(self.activity)}"
            + (f" · {safe(self.notes)}" if self.notes else "")
        )


def edit_embed(draft):
    embed = discord.Embed(
        title="Your LFG listing",
        description=(
            "Choose a detail to change from the dropdown. "
            "Your listing is only published when you select **Save Listing**.\n\n"
            + draft.describe()
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Only you can see this editor")
    return embed


class FieldTextModal(discord.ui.Modal):
    def __init__(self, editor, field):
        super().__init__(title="Character name" if field == "character" else "Extra notes")
        self.editor = editor
        self.field = field
        field_input = discord.ui.TextInput(
            label="Character name" if field == "character" else "Specific dungeon or notes",
            default=getattr(editor.draft, field),
            required=(field == "character"),
            max_length=32 if field == "character" else 80,
            style=discord.TextStyle.short,
        )
        self.add_item(field_input)
        self.input = field_input

    async def on_submit(self, interaction):
        value = self.input.value.strip()
        if self.field == "character" and not value:
            await interaction.response.send_message("Character name cannot be blank.", ephemeral=True)
            return
        setattr(self.editor.draft, self.field, value)
        await interaction.response.edit_message(embed=edit_embed(self.editor.draft), view=self.editor)


class FieldPicker(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose a detail to edit",
            options=[
                discord.SelectOption(label=label, value=value)
                for value, label in [
                    ("character", "Character name"), ("level", "Level (1–60)"),
                    ("class_name", "Class"), ("role", "Role"),
                    ("availability", "Availability"), ("activity", "Looking for"),
                    ("notes", "Dungeon / notes (optional)"),
                ]
            ],
        )

    async def callback(self, interaction):
        field = self.values[0]
        editor = self.view
        if field in ("character", "notes"):
            await interaction.response.send_modal(FieldTextModal(editor, field))
        else:
            await interaction.response.edit_message(
                embed=edit_embed(editor.draft),
                view=ValueView(editor, field),
            )


class LevelRangeSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose your level range",
            options=[
                discord.SelectOption(label=f"Levels {start}–{min(start+19, 60)}", value=str(start))
                for start in (1, 21, 41)
            ],
        )

    async def callback(self, interaction):
        await interaction.response.edit_message(
            embed=edit_embed(self.view.editor.draft),
            view=LevelValueView(self.view.editor, int(self.values[0])),
        )


class ValueSelect(discord.ui.Select):
    def __init__(self, field, values):
        self.field = field
        super().__init__(
            placeholder="Select " + field.replace("_", " "),
            options=[discord.SelectOption(label=str(v), value=str(v)) for v in values],
        )

    async def callback(self, interaction):
        draft = self.view.editor.draft
        value = int(self.values[0]) if self.field == "level" else self.values[0]
        setattr(draft, self.field, value)
        editor = self.view.editor
        await interaction.response.edit_message(embed=edit_embed(draft), view=editor)


class BackView(discord.ui.View):
    def __init__(self, editor):
        super().__init__(timeout=900)
        self.editor = editor

    async def interaction_check(self, interaction):
        return await self.editor.interaction_check(interaction)

    @discord.ui.button(label="Back to Editor", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(
            embed=edit_embed(self.editor.draft), view=self.editor
        )


class ValueView(BackView):
    def __init__(self, editor, field):
        super().__init__(editor)
        if field == "level":
            self.add_item(LevelRangeSelect())
        else:
            options = {
                "class_name": CLASSES, "role": ROLES,
                "availability": WHEN, "activity": WHAT,
            }[field]
            self.add_item(ValueSelect(field, options))


class LevelValueView(BackView):
    def __init__(self, editor, start):
        super().__init__(editor)
        self.add_item(ValueSelect("level", range(start, min(start + 20, 61))))


class EditorView(discord.ui.View):
    def __init__(self, cog, channel_id, owner_id):
        super().__init__(timeout=900)
        self.cog, self.channel_id, self.owner_id = cog, channel_id, owner_id
        self.draft = Draft(current_listing(channel_id, owner_id))
        self.add_item(FieldPicker())

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This is someone else's editor.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Save Listing", style=discord.ButtonStyle.success, row=1)
    async def save(self, interaction, button):
        if not self.draft.character:
            await interaction.response.send_message("Set your character name first.", ephemeral=True)
            return
        with connect() as db:
            db.execute(
                """INSERT INTO lfg_listings
                (channel_id,user_id,character,level,class_name,role,availability,activity,notes,board_day)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(channel_id,user_id) DO UPDATE SET
                character=excluded.character,level=excluded.level,
                class_name=excluded.class_name,role=excluded.role,
                availability=excluded.availability,activity=excluded.activity,
                notes=excluded.notes,board_day=excluded.board_day""",
                (self.channel_id, self.owner_id, self.draft.character, self.draft.level,
                 self.draft.class_name, self.draft.role, self.draft.availability,
                 self.draft.activity, self.draft.notes, today()),
            )
        await interaction.response.edit_message(
            content="Your listing has been saved to the board.",
            embed=edit_embed(self.draft), view=None,
        )
        await self.cog.refresh(self.channel_id)


class BoardView(discord.ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=None)
        self.cog, self.channel_id = cog, channel_id

    @discord.ui.button(
        label="Add / Update Me", style=discord.ButtonStyle.primary,
        custom_id="hyped_lfg_edit_v1",
    )
    async def edit(self, interaction, button):
        editor = EditorView(self.cog, self.channel_id, interaction.user.id)
        await interaction.response.send_message(
            embed=edit_embed(editor.draft), view=editor, ephemeral=True
        )

    @discord.ui.button(
        label="Remove Me", style=discord.ButtonStyle.secondary,
        custom_id="hyped_lfg_remove_v1",
    )
    async def remove(self, interaction, button):
        with connect() as db:
            result = db.execute(
                "DELETE FROM lfg_listings WHERE channel_id=? AND user_id=?",
                (self.channel_id, interaction.user.id),
            )
        await interaction.response.send_message(
            "You have been removed from the board." if result.rowcount else
            "You don't currently have a listing on this board.",
            ephemeral=True,
        )
        await self.cog.refresh(self.channel_id)


class LFGPreview(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    async def cog_load(self):
        init_db()
        with connect() as db:
            boards = db.execute("SELECT channel_id, message_id FROM lfg_boards").fetchall()
        for board in boards:
            self.bot.add_view(
                BoardView(self, board["channel_id"]), message_id=board["message_id"]
            )
        self.daily_refresh.start()

    async def cog_unload(self):
        self.daily_refresh.cancel()

    async def refresh(self, channel_id):
        async with self.lock:
            row = get_board(channel_id)
            if not row:
                return
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden):
                    return
            try:
                message = await channel.fetch_message(row["message_id"])
                await message.edit(embed=board_embed(channel_id))
            except (discord.NotFound, discord.Forbidden):
                pass

    @tasks.loop(minutes=1)
    async def daily_refresh(self):
        with connect() as db:
            rows = db.execute("SELECT channel_id FROM lfg_boards WHERE board_day != ?", (today(),)).fetchall()
            if rows:
                db.execute("DELETE FROM lfg_listings WHERE board_day != ?", (today(),))
                db.execute("UPDATE lfg_boards SET board_day=?", (today(),))
        for row in rows:
            await self.refresh(row["channel_id"])

    @daily_refresh.before_loop
    async def ready_for_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="lfg-preview", description="Create or reopen the interactive Hyped LFG test board")
    @app_commands.guilds(discord.Object(id=int(os.environ["HYPED_GUILD_ID"])))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def lfg_preview(self, interaction: discord.Interaction):
        if interaction.guild_id != int(os.environ["HYPED_GUILD_ID"]):
            await interaction.response.send_message("Only available in Hyped.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Use this in a server text channel.", ephemeral=True)
            return
        row = get_board(channel.id)
        if row:
            try:
                board_message = await channel.fetch_message(row["message_id"])
                await interaction.response.send_message(
                    f"Existing board: {board_message.jump_url}", ephemeral=True
                )
                await self.refresh(channel.id)
                return
            except discord.NotFound:
                pass
        await interaction.response.defer(ephemeral=True)
        msg = await channel.send(embed=board_embed(channel.id), view=BoardView(self, channel.id))
        with connect() as db:
            db.execute(
                "INSERT INTO lfg_boards(channel_id,guild_id,message_id,board_day) VALUES(?,?,?,?) "
                "ON CONFLICT(channel_id) DO UPDATE SET message_id=excluded.message_id,"
                "guild_id=excluded.guild_id,board_day=excluded.board_day",
                (channel.id, interaction.guild_id, msg.id, today()),
            )
        await interaction.followup.send(f"Board posted: {msg.jump_url}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(LFGPreview(bot))
