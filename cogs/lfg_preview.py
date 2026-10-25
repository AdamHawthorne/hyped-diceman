"""Hyped WoW: Forever launch-planning board, piloted through /lfg-preview.

The board is live in the chosen test channel and stored in its own SQLite file.
Existing dice, Flameboard and Forever-role systems are untouched.
"""
import asyncio
import os
from pathlib import Path
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

PATH = os.getenv(
    "LFG_DB_PATH",
    "/data/lfg_board.db" if Path("/data").is_dir() else "lfg_board.db",
)

RACES = ("Orc", "Troll", "Tauren", "Undead", "Skyborne (Windshaper)")
STARTING_ZONES = {
    "Orc": "Durotar",
    "Troll": "Durotar",
    "Tauren": "Mulgore",
    "Undead": "Tirisfal Glades",
    "Skyborne (Windshaper)": "Zephras Isle",
}
RACE_CLASSES = {
    "Orc": ("Hunter", "Mage", "Rogue", "Shaman", "Warlock", "Warrior"),
    "Troll": ("Hunter", "Mage", "Priest", "Rogue", "Shaman", "Warlock", "Warrior"),
    "Tauren": ("Druid", "Hunter", "Shaman", "Warrior"),
    "Undead": ("Mage", "Paladin", "Priest", "Rogue", "Warlock", "Warrior"),
    "Skyborne (Windshaper)": ("Druid", "Hunter", "Rogue", "Shaman", "Warrior"),
}
CLASS_ROLES = {
    "Druid": ("Tank", "Healer", "DPS"),
    "Paladin": ("Tank", "Healer", "DPS"),
    "Warrior": ("Tank", "DPS"),
    "Priest": ("Healer", "DPS"),
    "Shaman": ("Healer", "DPS"),
    "Hunter": ("DPS",),
    "Rogue": ("DPS",),
    "Mage": ("DPS",),
    "Warlock": ("DPS",),
}
INTENTIONS = (
    "Solo Questing",
    "Solo Questing + Dungeons (LFG/ Pugging)",
    "Group Questing",
    "Group Questing + Dungeons",
    "Grinding (Dungeons, grind spots etc)",
    "Flexible",
)

START_TIMES = (
    "Launch 23:00 UK",
    "00:00 UK", "01:00 UK", "02:00 UK", "03:00 UK", "04:00 UK",
    "05:00 UK", "06:00 UK", "07:00 UK", "08:00 UK", "09:00 UK",
    "10:00 UK", "11:00 UK", "12:00 UK", "13:00 UK", "14:00 UK",
    "15:00 UK", "16:00 UK", "17:00 UK", "18:00 UK", "19:00 UK",
    "20:00 UK", "21:00 UK", "22:00 UK",
    "Later",
)

FINISH_TIMES = (
    "00:00 UK", "01:00 UK", "02:00 UK", "03:00 UK", "04:00 UK",
    "05:00 UK", "06:00 UK", "07:00 UK", "08:00 UK", "09:00 UK",
    "10:00 UK", "11:00 UK", "12:00 UK", "13:00 UK", "14:00 UK",
    "15:00 UK", "16:00 UK", "17:00 UK", "18:00 UK", "19:00 UK",
    "20:00 UK", "21:00 UK", "22:00 UK", "23:00 UK",
    "Long session...",
)

LONG_SESSION_TIMES = ("24 hours", "30 hours", "40 hours", "50 hours +")

LAUNCH_DAYS = (
    "Launch Night · Wed 4 Nov",
    "Thu 5 Nov",
    "Fri 6 Nov",
    "Sat 7 Nov",
    "Sun 8 Nov",
    "Mon 9 Nov",
    "Tue 10 Nov",
    "Wed 11 Nov",
    "Thu 12 Nov",
    "Fri 13 Nov",
    "Sat 14 Nov",
    "Sun 15 Nov",
)

HEADER_IMAGE = "https://i.vgy.me/2Jt4oi.png"
ACCENT = 0x8C5A3C


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
            board_day TEXT NOT NULL DEFAULT 'launch'
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS lfg_listings (
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            character TEXT NOT NULL DEFAULT '',
            level INTEGER NOT NULL DEFAULT 1,
            level_label TEXT NOT NULL DEFAULT '1',
            level_sort INTEGER NOT NULL DEFAULT 1,
            class_name TEXT NOT NULL,
            role TEXT NOT NULL,
            availability TEXT NOT NULL,
            activity TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            avatar_url TEXT NOT NULL DEFAULT '',
            race TEXT NOT NULL DEFAULT '',
            starting_zone TEXT NOT NULL DEFAULT '',
            days_off TEXT NOT NULL DEFAULT '',
            board_day TEXT NOT NULL DEFAULT 'launch',
            PRIMARY KEY(channel_id, user_id)
        )""")

        columns = {row[1] for row in db.execute("PRAGMA table_info(lfg_listings)")}
        additions = {
            "level_label": "TEXT NOT NULL DEFAULT '1'",
            "level_sort": "INTEGER NOT NULL DEFAULT 1",
            "avatar_url": "TEXT NOT NULL DEFAULT ''",
            "race": "TEXT NOT NULL DEFAULT ''",
            "starting_zone": "TEXT NOT NULL DEFAULT ''",
            "days_off": "TEXT NOT NULL DEFAULT ''",
        }
        for name, declaration in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE lfg_listings ADD COLUMN {name} {declaration}")

        # Pre-launch plans persist until a member updates or removes them.
        db.execute("UPDATE lfg_listings SET board_day='launch'")
        db.execute("UPDATE lfg_boards SET board_day='launch'")


def get_board(channel_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM lfg_boards WHERE channel_id=?",
            (channel_id,),
        ).fetchone()


def current_listing(channel_id, user_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM lfg_listings WHERE channel_id=? AND user_id=?",
            (channel_id, user_id),
        ).fetchone()


def entries(channel_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM lfg_listings WHERE channel_id=? "
            "ORDER BY character COLLATE NOCASE ASC",
            (channel_id,),
        ).fetchall()


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def player_avatar_url(row, guild=None):
    if row["avatar_url"]:
        return row["avatar_url"]
    if guild is not None:
        member = guild.get_member(row["user_id"])
        if member is not None:
            return str(member.display_avatar.url)
    return ""


def format_roles(value):
    return " / ".join(part.strip() for part in value.split(",") if part.strip())


def compact_availability(value):
    return " / ".join(part.strip() for part in value.replace("\r", "").split("\n") if part.strip())


def compact_days(value):
    return value.replace(" Nov", "")


class Draft:
    def __init__(self, row=None):
        self.character = row["character"] if row else ""
        self.race = row["race"] if row else ""
        self.starting_zone = row["starting_zone"] if row else ""
        self.class_name = row["class_name"] if row else ""
        self.role = row["role"] if row else ""
        self.availability = row["availability"] if row else ""
        self.activity = row["activity"] if row else ""
        self.days_off = row["days_off"] if row else ""
        self.notes = row["notes"] if row else ""

    def summary(self):
        name = safe(self.character) if self.character else "Undecided"
        fields = [
            f"**Planned character:** {name}",
            f"**Race:** {self.race or 'Not set'}",
            f"**Class:** {self.class_name or 'Not set'}",
            f"**Role(s):** {format_roles(self.role) or 'Not set'}",
            f"**Playing:** {safe(compact_availability(self.availability) or 'Not set')}",
            f"**Days:** {safe(self.days_off or 'Not set')}",
            f"**Levelling plan:** {safe(self.activity or 'Not set')}",
            f"**Notes:** {safe(self.notes or 'None')}",
        ]
        if self.starting_zone:
            fields.insert(2, f"**Starting zone:** {self.starting_zone}")
        return "\n".join(fields)


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


def wizard_embed(title, prompt, state):
    embed = discord.Embed(
        title=title,
        description=f"{prompt}\n\n{state.draft.summary()}",
        color=ACCENT,
    )
    embed.set_footer(text="Only you can see this. Nothing changes until you save.")
    return embed


class PlannedNameModal(discord.ui.Modal, title="What are your plans?"):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.character = discord.ui.TextInput(
            label="Character name",
            placeholder="Forename Surname",
            default=state.draft.character or None,
            required=False,
            max_length=48,
        )
        self.add_item(self.character)

    async def on_submit(self, interaction):
        self.state.draft.character = " ".join(self.character.value.split())
        await interaction.response.send_message(
            embed=wizard_embed(
                "Hyped Ogre LFG · Race",
                "What race you planning?",
                self.state,
            ),
            view=RaceView(self.state),
            ephemeral=True,
        )


class RaceSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose planned race",
            options=[
                discord.SelectOption(
                    label=race,
                    value=race,
                    description=STARTING_ZONES[race],
                )
                for race in RACES
            ],
        )

    async def callback(self, interaction):
        state = self.view.state
        race = self.values[0]
        state.draft.race = race
        state.draft.starting_zone = STARTING_ZONES[race]
        if state.draft.class_name not in RACE_CLASSES[race]:
            state.draft.class_name = ""
            state.draft.role = ""

        await interaction.response.edit_message(
            embed=wizard_embed(
                "Hyped Ogre LFG · Class",
                f"Starting in {state.draft.starting_zone}. What class?",
                state,
            ),
            view=ClassView(state),
        )


class RaceView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(RaceSelect())

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class ClassSelect(discord.ui.Select):
    def __init__(self, race):
        super().__init__(
            placeholder="Choose planned class",
            options=[
                discord.SelectOption(label=value, value=value)
                for value in RACE_CLASSES[race]
            ],
        )

    async def callback(self, interaction):
        state = self.view.state
        class_name = self.values[0]
        state.draft.class_name = class_name
        allowed_roles = CLASS_ROLES[class_name]

        if len(allowed_roles) == 1:
            state.draft.role = allowed_roles[0]
            await interaction.response.edit_message(
                embed=wizard_embed(
                    "Hyped Ogre LFG · Start Time",
                    "When you starting?",
                    state,
                ),
                view=StartTimeView(state),
            )
            return

        state.draft.role = ""
        await interaction.response.edit_message(
            embed=wizard_embed(
                "Hyped Ogre LFG · Roles",
                "What role(s) you happy to play? Choose all that apply.",
                state,
            ),
            view=RoleView(state, allowed_roles),
        )


class ClassView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(ClassSelect(state.draft.race))

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class RoleSelect(discord.ui.Select):
    def __init__(self, allowed_roles):
        super().__init__(
            placeholder="Choose role(s)",
            min_values=1,
            max_values=len(allowed_roles),
            options=[
                discord.SelectOption(label=value, value=value)
                for value in allowed_roles
            ],
        )

    async def callback(self, interaction):
        state = self.view.state
        state.draft.role = ", ".join(self.values)
        await interaction.response.edit_message(
            embed=wizard_embed(
                "Hyped Ogre LFG · Start Time",
                "When you starting?",
                state,
            ),
            view=StartTimeView(state),
        )


class RoleView(discord.ui.View):
    def __init__(self, state, allowed_roles):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(RoleSelect(allowed_roles))

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class StartTimeSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose start time",
            options=[discord.SelectOption(label=value, value=value) for value in START_TIMES],
        )

    async def callback(self, interaction):
        state = self.view.state
        state.start_time = self.values[0]
        await interaction.response.edit_message(
            embed=wizard_embed(
                "Hyped Ogre LFG · Finish Time",
                f"Starting {state.start_time}. When you stopping?",
                state,
            ),
            view=FinishTimeView(state),
        )


class StartTimeView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(StartTimeSelect())

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class FinishTimeSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose finish time",
            options=[discord.SelectOption(label=value, value=value) for value in FINISH_TIMES],
        )

    async def callback(self, interaction):
        state = self.view.state
        finish_time = self.values[0]
        if finish_time == "Long session...":
            await interaction.response.edit_message(
                embed=wizard_embed(
                    "Hyped Ogre LFG · Long Session",
                    "How long you planning to go?",
                    state,
                ),
                view=LongSessionView(state),
            )
            return

        start_time = getattr(state, "start_time", "Later")
        state.draft.availability = f"{start_time} - {finish_time}"
        await interaction.response.edit_message(
            embed=wizard_embed(
                "Hyped Ogre LFG · Playing On",
                "Which days you planning to play? Choose any that apply.",
                state,
            ),
            view=LaunchDaysView(state),
        )


class FinishTimeView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(FinishTimeSelect())

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class LongSessionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose session length",
            options=[discord.SelectOption(label=value, value=value) for value in LONG_SESSION_TIMES],
        )

    async def callback(self, interaction):
        state = self.view.state
        start_time = getattr(state, "start_time", "Later")
        state.draft.availability = f"{start_time} + {self.values[0]}"
        await interaction.response.edit_message(
            embed=wizard_embed(
                "Hyped Ogre LFG · Playing On",
                "Which days you planning to play? Choose any that apply.",
                state,
            ),
            view=LaunchDaysView(state),
        )


class LongSessionView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(LongSessionSelect())

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class LaunchDaysSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Playing on",
            min_values=1,
            max_values=len(LAUNCH_DAYS),
            options=[discord.SelectOption(label=value, value=value) for value in LAUNCH_DAYS],
        )

    async def callback(self, interaction):
        state = self.view.state
        state.draft.days_off = ", ".join(self.values)
        await interaction.response.edit_message(
            embed=wizard_embed(
                "Hyped Ogre LFG · Levelling Plan",
                "How you planning to level? Choose all that apply.",
                state,
            ),
            view=IntentionView(state),
        )


class LaunchDaysView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(LaunchDaysSelect())

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)

class IntentionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose levelling plan(s)",
            min_values=1,
            max_values=len(INTENTIONS),
            options=[
                discord.SelectOption(label=value, value=value)
                for value in INTENTIONS
            ],
        )

    async def callback(self, interaction):
        state = self.view.state
        state.draft.activity = ", ".join(self.values)
        await interaction.response.send_modal(NotesModal(state))


class IntentionView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state
        self.add_item(IntentionSelect())

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)


class NotesModal(discord.ui.Modal, title="Anything else?"):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.notes = discord.ui.TextInput(
            label="Notes (optional)",
            placeholder="e.g. 4 days booked off, LF gamers",
            default=state.draft.notes or None,
            required=False,
            max_length=120,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.notes)

    async def on_submit(self, interaction):
        self.state.draft.notes = " ".join(self.notes.value.split())
        await interaction.response.edit_message(
            embed=review_embed(self.state),
            view=ReviewView(self.state),
        )

def review_embed(state):
    embed = discord.Embed(
        title="Hyped Ogre LFG · Review",
        description="**Ogre got plan.** Save it to board?\n\n" + state.draft.summary(),
        color=ACCENT,
    )
    embed.set_footer(text="Only you can see this review")
    return embed


class ReviewView(discord.ui.View):
    def __init__(self, state):
        super().__init__(timeout=900)
        self.state = state

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success)
    async def save(self, interaction, button):
        draft = self.state.draft
        required = (
            draft.race,
            draft.class_name,
            draft.role,
            draft.availability,
            draft.activity,
            draft.days_off,
        )
        if not all(required):
            await interaction.response.send_message(
                "Ogre missing something. Press Add / Update and run through plan again.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        with connect() as db:
            db.execute(
                """INSERT INTO lfg_listings
                (channel_id,user_id,character,level,level_label,level_sort,class_name,role,
                 availability,activity,notes,avatar_url,race,starting_zone,days_off,board_day)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(channel_id,user_id) DO UPDATE SET
                character=excluded.character,
                level=1,
                level_label='1',
                level_sort=1,
                class_name=excluded.class_name,
                role=excluded.role,
                availability=excluded.availability,
                activity=excluded.activity,
                avatar_url=excluded.avatar_url,
                race=excluded.race,
                starting_zone=excluded.starting_zone,
                days_off=excluded.days_off,
                board_day='launch'""",
                (
                    self.state.channel_id,
                    self.state.owner_id,
                    draft.character,
                    1,
                    "1",
                    1,
                    draft.class_name,
                    draft.role,
                    draft.availability,
                    draft.activity,
                    draft.notes,
                    str(interaction.user.display_avatar.url),
                    draft.race,
                    draft.starting_zone,
                    draft.days_off,
                    "launch",
                ),
            )

        try:
            await self.state.cog.refresh(self.state.channel_id)
            message = "Saved. Ogre updated board."
        except Exception as exc:
            print(
                f"LFG BOARD REFRESH FAILED channel={self.state.channel_id} "
                f"user={self.state.owner_id} error={exc!r}"
            )
            message = "Plan saved, but Ogre could not refresh board. Tell an officer."

        await interaction.edit_original_response(
            content=message,
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Start Again", style=discord.ButtonStyle.secondary)
    async def restart(self, interaction, button):
        await interaction.response.send_modal(PlannedNameModal(self.state))


class AddUpdateButton(discord.ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(
            label="Add / Update",
            style=discord.ButtonStyle.secondary,
            custom_id="hyped_lfg_edit_v4",
        )
        self.cog = cog
        self.channel_id = channel_id

    async def callback(self, interaction):
        state = WizardState(self.cog, self.channel_id, interaction.user.id)
        await interaction.response.send_modal(PlannedNameModal(state))


class RemoveButton(discord.ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(
            label="Remove",
            style=discord.ButtonStyle.secondary,
            custom_id="hyped_lfg_remove_v4",
        )
        self.cog = cog
        self.channel_id = channel_id

    async def callback(self, interaction):
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
            text = "Ogre removed your plan."
        elif result.rowcount:
            text = "Plan removed, but Ogre could not refresh board."
        else:
            text = "You don't have a plan on board."

        await interaction.response.send_message(text, ephemeral=True)


class BoardLayout(discord.ui.LayoutView):
    def __init__(self, cog, channel_id, guild=None):
        super().__init__(timeout=None)
        roster = entries(channel_id)
        visible = roster[:8]

        header = discord.ui.Section(
            discord.ui.TextDisplay(
                "## <Hyped> LFG-LFM Board\n"
                "**Ogre want know launch plans.**"
            ),
            accessory=discord.ui.Thumbnail(
                HEADER_IMAGE,
                description="Hyped LFG-LFM",
            ),
        )
        container = discord.ui.Container(header, accent_color=ACCENT)

        if visible:
            container.add_item(discord.ui.Separator())
            for index, row in enumerate(visible):
                race = row["race"] or "Race not set"
                zone = row["starting_zone"] or STARTING_ZONES.get(race, "Starting zone not set")
                roles = format_roles(row["role"]) or "Role not set"
                days = row["days_off"] or "Not set"
                character = safe(row["character"]) if row["character"] else "Name TBD"
                notes = safe(row["notes"]) if row["notes"] else ""
                lines = [
                    f"**{character} · {race} {row['class_name']} · {roles}**",
                    f"-# **Playing:** {safe(compact_availability(row['availability']))} · {safe(compact_days(days))}",
                    f"-# **Plan:** {safe(row['activity'])}",
                ]
                if notes:
                    lines.append(f"-# **Notes:** {notes}")
                lines.append(f"-# **Starting Zone:** {zone}")
                text = "\n".join(lines)
                avatar = player_avatar_url(row, guild)
                thumbnail = discord.ui.Thumbnail(
                    avatar or HEADER_IMAGE,
                    description=f"{character} Discord avatar",
                )
                container.add_item(
                    discord.ui.Section(
                        discord.ui.TextDisplay(text),
                        accessory=thumbnail,
                    )
                )
                if index != len(visible) - 1:
                    container.add_item(discord.ui.Separator())
        else:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("*Nobody on board yet.*"))

        if len(roster) > len(visible):
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    f"-# {len(roster) - len(visible)} more plan(s) not shown on this test board."
                )
            )

        self.add_item(container)

        self.add_item(
            discord.ui.ActionRow(
                AddUpdateButton(cog, channel_id),
                RemoveButton(cog, channel_id),
            )
        )


class LFGPreview(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    async def cog_load(self):
        init_db()
        with connect() as db:
            boards = db.execute(
                "SELECT channel_id, guild_id, message_id FROM lfg_boards"
            ).fetchall()
        for board in boards:
            self.bot.add_view(
                BoardLayout(
                    self,
                    board["channel_id"],
                    self.bot.get_guild(board["guild_id"]),
                ),
                message_id=board["message_id"],
            )

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
                content=None,
                embeds=[],
                attachments=[],
                view=BoardLayout(
                    self,
                    channel_id,
                    getattr(channel, "guild", None),
                ),
            )

    async def post_new_board(self, interaction, channel):
        try:
            await interaction.response.send_message(
                view=BoardLayout(self, channel.id, interaction.guild),
            )
            msg = await interaction.original_response()
            with connect() as db:
                db.execute(
                    "INSERT INTO lfg_boards(channel_id,guild_id,message_id,board_day) "
                    "VALUES(?,?,?,'launch') "
                    "ON CONFLICT(channel_id) DO UPDATE SET "
                    "message_id=excluded.message_id,"
                    "guild_id=excluded.guild_id,"
                    "board_day='launch'",
                    (channel.id, interaction.guild_id, msg.id),
                )
        except Exception as exc:
            print(f"LFG BOARD POST FAILED channel={channel.id} error={exc!r}")
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Ogre couldn't finish setting up board. Check Fly log.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Ogre couldn't post board. Check Fly log.",
                    ephemeral=True,
                )

    @app_commands.command(
        name="lfg-preview",
        description="Create or reopen the Hyped Forever launch-planning board",
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
                    f"Ogre found board: {board_message.jump_url}",
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
