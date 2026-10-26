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

from .lfg_sort import listing_board_key, is_solo_or_sorted, is_open_lfm
from .lfg_pagination import PAGE_SIZE, page_count, clamp_page, page_rows
from .lfg_demo import demo_entries, display_race, display_uk_times

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
    "Open / Flexible",
    "Solo Questing",
    "Solo Questing + Dungeons (LFG/ Pugging)",
    "Group Questing (LFM)",
    "Group Questing + Dungeons (LFM)",
    "Grinding (Dungeons, grind spots etc)",
    "Full Group / Sorted",
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
BOTTOM_IMAGE = "https://i.vgy.me/WGNGdr.png"
# Native Unicode emoji stays sharp and requires no guild emoji permissions.
OPEN_LFM_MARKER = "🟢"
ROLE_EMOJIS = {"Tank": "🛡️", "Healer": "✚", "DPS": "⚔️"}
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
            board_day TEXT NOT NULL DEFAULT 'launch',
            page_index INTEGER NOT NULL DEFAULT 0
        )""")
        # Safe additive migration: no board IDs or player details are reset.
        board_columns = {row[1] for row in db.execute("PRAGMA table_info(lfg_boards)")}
        if "page_index" not in board_columns:
            db.execute("ALTER TABLE lfg_boards ADD COLUMN page_index INTEGER NOT NULL DEFAULT 0")
        db.execute("""CREATE TABLE IF NOT EXISTS lfg_board_parts (
            channel_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY(channel_id, position)
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
        roster = db.execute(
            "SELECT * FROM lfg_listings WHERE channel_id=?",
            (channel_id,),
        ).fetchall()
    # Recompute after every save/update; the board uses entries() on refresh.
    return sorted(roster, key=listing_board_key)


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


def format_roles(value, *, icons=False):
    roles = [part.strip() for part in value.split(",") if part.strip()]
    if icons:
        return " / ".join(
            f"{role} {ROLE_EMOJIS[role]}" if role in ROLE_EMOJIS else role
            for role in roles
        )
    return " / ".join(roles)


def show_lfm_emoji(activity):
    """The marker and board sorting use the same Open/LFM rule."""
    return is_open_lfm(activity)


def contact_line(row, *, preview=False):
    """Clickable member contact without exposing fake IDs in demo previews."""
    if preview:
        return "-# **Contact:** @Demo player"
    return f"-# **Contact:** <@{int(row['user_id'])}>"


def compact_availability(value):
    return " / ".join(part.strip() for part in value.replace("\r", "").split("\n") if part.strip())


def compact_days(value):
    return value.replace(" Nov", "")


def automatic_start_days(start_time):
    if start_time == "Launch 23:00 UK":
        return (LAUNCH_DAYS[0],)
    if start_time in START_TIMES and start_time != "Later":
        return (LAUNCH_DAYS[1],)
    return ()


def selectable_playing_days(start_time):
    # Only launch night is hidden; Thursday must remain selectable.
    return tuple(LAUNCH_DAYS[1:])


def merge_playing_days(start_time, selected):
    allowed = selectable_playing_days(start_time)
    chosen = set(automatic_start_days(start_time))
    chosen.update(day for day in selected if day in allowed)
    return ", ".join(day for day in LAUNCH_DAYS if day in chosen)


def playing_days_prompt(state):
    automatic = automatic_start_days(getattr(state, "start_time", "Later"))
    if automatic:
        return (
            f"{automatic[0]} is already included from your start time. "
            "Select your playing days below, including any extra days."
        )
    return "Choose the days you plan to play, from Thu 5 Nov onwards."


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
            f"**Playing:** {safe(display_uk_times(compact_availability(self.availability) or 'Not set'))}",
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
        start, separator, finish = self.draft.availability.partition(" - ")
        if not separator:
            start, separator, finish = self.draft.availability.partition(" + ")
        self.start_time = start if start in START_TIMES else ""
        self.finish_time = finish if finish in FINISH_TIMES + LONG_SESSION_TIMES else ""

    def set_availability(self):
        if self.start_time == "Later":
            self.draft.availability = "Later"
        elif self.start_time in START_TIMES and self.finish_time in FINISH_TIMES + LONG_SESSION_TIMES:
            joiner = " + " if self.finish_time in LONG_SESSION_TIMES else " - "
            self.draft.availability = self.start_time + joiner + self.finish_time
        else:
            self.draft.availability = ""

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
            embed=step_embed(self.state, "race"),
            view=RaceView(self.state),
            ephemeral=True,
        )


STAGE_NAMES = {
    "race": ("Race", "What race you planning?"),
    "class": ("Class", "What class you planning?"),
    "roles": ("Roles", "What role(s) you happy to play? Choose all that apply."),
    "start": ("Start Time", "When you starting?"),
    "finish": ("Finish Time", "When you stopping?"),
    "long": ("Long Session", "How long you planning to go?"),
    "days": ("Playing On", None),
    "intentions": ("Levelling Plan", "How you planning to level? Choose all that apply."),
}


def step_embed(state, stage):
    title, prompt = STAGE_NAMES[stage]
    if stage == "days":
        prompt = playing_days_prompt(state)
    if stage == "finish":
        prompt = f"Starting {state.start_time}. When you stopping?"
    return wizard_embed(f"Hyped Ogre LFG · {title}", prompt, state)


def next_step(state, stage):
    if stage == "race":
        return "class"
    if stage == "class":
        return "roles" if len(CLASS_ROLES[state.draft.class_name]) > 1 else "start"
    if stage == "roles":
        return "start"
    if stage == "start":
        return "days" if state.start_time == "Later" else "finish"
    if stage == "finish":
        return "long" if state.finish_time == "Long session..." else "days"
    if stage == "long":
        return "days"
    if stage == "days":
        return "intentions"
    return None


def previous_step(state, stage):
    if stage == "race":
        return "name"
    if stage == "class":
        return "race"
    if stage == "roles":
        return "class"
    if stage == "start":
        return "roles" if len(CLASS_ROLES[state.draft.class_name]) > 1 else "class"
    if stage == "finish":
        return "start"
    if stage == "long":
        return "finish"
    if stage == "days":
        if state.start_time == "Later":
            return "start"
        return "long" if state.finish_time in LONG_SESSION_TIMES else "finish"
    if stage == "intentions":
        return "days"
    return None


def make_step_view(state, stage):
    return {
        "race": RaceView,
        "class": ClassView,
        "roles": RoleView,
        "start": StartTimeView,
        "finish": FinishTimeView,
        "long": LongSessionView,
        "days": LaunchDaysView,
        "intentions": IntentionView,
    }[stage](state)


async def show_step(interaction, state, stage):
    await interaction.response.edit_message(
        embed=step_embed(state, stage),
        view=make_step_view(state, stage),
    )


def planned_options(state, stage):
    draft = state.draft
    if stage == "race":
        return RACES, {draft.race}
    if stage == "class":
        return RACE_CLASSES[draft.race], {draft.class_name}
    if stage == "roles":
        return CLASS_ROLES[draft.class_name], set(draft.role.split(", "))
    if stage == "start":
        return START_TIMES, {state.start_time}
    if stage == "finish":
        chosen = ("Long session..." if state.finish_time in LONG_SESSION_TIMES
                  else state.finish_time)
        return FINISH_TIMES, {chosen}
    if stage == "long":
        return LONG_SESSION_TIMES, {state.finish_time}
    if stage == "days":
        return selectable_playing_days(state.start_time), set(draft.days_off.split(", "))
    if stage == "intentions":
        # Match exact option strings: the Grinding label itself contains a comma.
        return INTENTIONS, {opt for opt in INTENTIONS if opt in draft.activity}
    raise ValueError(f"Unknown wizard stage {stage}")


class StageSelect(discord.ui.Select):
    def __init__(self, state, stage):
        self.stage = stage
        options, selected = planned_options(state, stage)
        multi = stage in ("roles", "days", "intentions")
        descriptions = {
            value: ("Wed 4 Nov" if value == "Launch 23:00 UK"
                    else "Choose playing days later" if value == "Later"
                    else "Thu 5 Nov")
            for value in START_TIMES
        } if stage == "start" else {}
        super().__init__(
            placeholder={
                "race": "Choose planned race",
                "class": "Choose planned class",
                "roles": "Choose role(s)",
                "start": "Choose start time",
                "finish": "Choose finish time",
                "long": "Choose session length",
                "days": "Playing on",
                "intentions": "Choose levelling plan(s)",
            }[stage],
            min_values=1,
            max_values=len(options) if multi else 1,
            options=[
                discord.SelectOption(
                    label=value,
                    value=value,
                    description=descriptions.get(value),
                    default=value in selected,
                ) for value in options
            ],
        )

    async def callback(self, interaction):
        state = self.view.state
        choice = self.values[0]
        draft = state.draft

        if self.stage == "race":
            draft.race = choice
            draft.starting_zone = STARTING_ZONES[choice]
            if draft.class_name not in RACE_CLASSES[choice]:
                draft.class_name = ""
                draft.role = ""
        elif self.stage == "class":
            draft.class_name = choice
            allowed = CLASS_ROLES[choice]
            chosen = [r for r in draft.role.split(", ") if r in allowed]
            draft.role = ", ".join(chosen) if len(allowed) > 1 else allowed[0]
        elif self.stage == "roles":
            draft.role = ", ".join(self.values)
        elif self.stage == "start":
            state.start_time = choice
            draft.days_off = merge_playing_days(
                choice, draft.days_off.split(", "))
            if choice == "Later":
                state.finish_time = ""
                state.set_availability()
            elif state.finish_time in FINISH_TIMES[:-1] or state.finish_time in LONG_SESSION_TIMES:
                state.set_availability()
            else:
                draft.availability = ""
        elif self.stage == "finish":
            state.finish_time = choice
            state.set_availability()
        elif self.stage == "long":
            state.finish_time = choice
            state.set_availability()
        elif self.stage == "days":
            draft.days_off = merge_playing_days(state.start_time, self.values)
        elif self.stage == "intentions":
            draft.activity = ", ".join(self.values)

        # Don't advance on selection: keep multiselect edits open until Next.
        await show_step(interaction, state, self.stage)


class RaceSelect(StageSelect):
    def __init__(self, state):
        super().__init__(state, "race")


class ClassSelect(StageSelect):
    def __init__(self, state):
        super().__init__(state, "class")


class RoleSelect(StageSelect):
    def __init__(self, state):
        super().__init__(state, "roles")


class StartTimeSelect(StageSelect):
    def __init__(self, state):
        super().__init__(state, "start")


class FinishTimeSelect(StageSelect):
    def __init__(self, state):
        super().__init__(state, "finish")


class LongSessionSelect(StageSelect):
    def __init__(self, state):
        super().__init__(state, "long")


class LaunchDaysSelect(StageSelect):
    def __init__(self, state):
        super().__init__(state, "days")


class IntentionSelect(StageSelect):
    def __init__(self, state):
        super().__init__(state, "intentions")


class WizardStepView(discord.ui.View):
    def __init__(self, state, stage, selector):
        super().__init__(timeout=900)
        self.state = state
        self.stage = stage
        self.add_item(selector(state))
        back = discord.ui.Button(
            label="‹ Back", style=discord.ButtonStyle.secondary, row=1,
        )
        back.callback = self.back
        self.add_item(back)
        forward = discord.ui.Button(
            label="Next ›", style=discord.ButtonStyle.secondary, row=1,
        )
        forward.callback = self.forward
        self.add_item(forward)

    async def interaction_check(self, interaction):
        return await self.state.check(interaction)

    async def back(self, interaction):
        previous = previous_step(self.state, self.stage)
        if previous == "name":
            await interaction.response.send_modal(PlannedNameModal(self.state))
            return
        await show_step(interaction, self.state, previous)

    async def forward(self, interaction):
        draft = self.state.draft
        stage = self.stage
        valid = {
            "race": bool(draft.race),
            "class": draft.class_name in RACE_CLASSES.get(draft.race, ()),
            "roles": bool(draft.role),
            "start": self.state.start_time in START_TIMES,
            "finish": bool(self.state.finish_time),
            "long": self.state.finish_time in LONG_SESSION_TIMES,
            "days": bool(draft.days_off),
            "intentions": bool(draft.activity),
        }[stage]

        if not valid:
            await interaction.response.send_message(
                "Choose an option before moving on.", ephemeral=True,
            )
            return

        if stage == "intentions":
            await interaction.response.send_modal(NotesModal(self.state))
            return

        await show_step(interaction, self.state, next_step(self.state, stage))


class RaceView(WizardStepView):
    def __init__(self, state):
        super().__init__(state, "race", RaceSelect)


class ClassView(WizardStepView):
    def __init__(self, state):
        super().__init__(state, "class", ClassSelect)


class RoleView(WizardStepView):
    def __init__(self, state, allowed_roles=None):
        super().__init__(state, "roles", RoleSelect)


class StartTimeView(WizardStepView):
    def __init__(self, state):
        super().__init__(state, "start", StartTimeSelect)


class FinishTimeView(WizardStepView):
    def __init__(self, state):
        super().__init__(state, "finish", FinishTimeSelect)


class LongSessionView(WizardStepView):
    def __init__(self, state):
        super().__init__(state, "long", LongSessionSelect)


class LaunchDaysView(WizardStepView):
    def __init__(self, state):
        super().__init__(state, "days", LaunchDaysSelect)


class IntentionView(WizardStepView):
    def __init__(self, state):
        super().__init__(state, "intentions", IntentionSelect)


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

    @discord.ui.button(label="‹ Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await show_step(interaction, self.state, "intentions")

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
                notes=excluded.notes,
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



def save_character_edit(channel_id, user_id, original, race, class_name, role):
    """Update only character choices, rejecting a stale or deleted listing."""
    if class_name not in RACE_CLASSES.get(race, ()):
        raise ValueError("Class is not available for this race")
    roles = [part.strip() for part in role.split(",") if part.strip()]
    if not roles or any(part not in CLASS_ROLES[class_name] for part in roles):
        raise ValueError("Choose valid roles for this class")
    with connect() as db:
        result = db.execute(
            """UPDATE lfg_listings SET race=?, starting_zone=?, class_name=?, role=?
            WHERE channel_id=? AND user_id=? AND race=? AND starting_zone=?
              AND class_name=? AND role=?""",
            (race, STARTING_ZONES[race], class_name, role, channel_id, user_id,
             original["race"], original["starting_zone"],
             original["class_name"], original["role"]),
        )
        return result.rowcount == 1


class CharacterEditSelect(discord.ui.Select):
    def __init__(self, editor, stage):
        self.editor = editor
        self.stage = stage
        if stage == "race":
            values = RACES
            selected = (editor.race,)
        elif stage == "class":
            values = RACE_CLASSES[editor.race]
            selected = (editor.class_name,)
        else:
            values = CLASS_ROLES[editor.class_name]
            selected = tuple(part.strip() for part in editor.role.split(","))
        super().__init__(
            placeholder={"race": "Choose race", "class": "Choose class",
                         "roles": "Choose role(s)"}[stage],
            min_values=1,
            max_values=len(values) if stage == "roles" else 1,
            options=[discord.SelectOption(label=value, value=value,
                                          default=value in selected)
                     for value in values],
        )

    async def callback(self, interaction):
        editor = self.editor
        if self.stage == "race":
            editor.race = self.values[0]
            if editor.class_name not in RACE_CLASSES[editor.race]:
                editor.class_name = ""
                editor.role = ""
                editor.show_stage("class")
            else:
                editor.show_stage("review")
        elif self.stage == "class":
            editor.class_name = self.values[0]
            allowed = CLASS_ROLES[editor.class_name]
            if len(allowed) == 1:
                editor.role = allowed[0]
                editor.show_stage("review")
            else:
                editor.show_stage("roles")
        else:
            editor.role = ", ".join(self.values)
            editor.show_stage("review")
        await interaction.response.edit_message(content=editor.summary(), view=editor)


class CharacterEditView(discord.ui.View):
    def __init__(self, cog, channel_id, user_id, row, stage):
        super().__init__(timeout=900)
        self.cog = cog
        self.channel_id = channel_id
        self.user_id = user_id
        self.original = dict(row)
        self.race = row["race"]
        self.class_name = row["class_name"]
        self.role = row["role"]
        self.show_stage(stage if self.race in RACE_CLASSES else "race")

    async def interaction_check(self, interaction):
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "This edit belongs to someone else.", ephemeral=True)
        return False

    def summary(self):
        prompt = {
            "race": "Choose your new race.",
            "class": "Choose a class available for your race.",
            "roles": "Choose your roles for this class.",
            "review": "Save these character choices?",
        }[self.stage]
        return (
            f"**{prompt}**\n"
            f"**Race:** {self.race or 'Not set'}\n"
            f"**Class:** {self.class_name or 'Not set'}\n"
            f"**Roles:** {format_roles(self.role) or 'Not set'}\n"
            f"**Starting Zone:** {STARTING_ZONES.get(self.race, 'Not set')}\n\n"
            "Your name, playing times, days, plan and notes stay as they are."
        )

    def show_stage(self, stage):
        self.stage = stage
        self.clear_items()
        if stage != "review":
            self.add_item(CharacterEditSelect(self, stage))
        else:
            save = discord.ui.Button(label="Save", style=discord.ButtonStyle.success)
            save.callback = self.save
            self.add_item(save)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self.cancel
        self.add_item(cancel)

    async def save(self, interaction):
        await interaction.response.defer()
        saved = save_character_edit(
            self.channel_id, self.user_id, self.original,
            self.race, self.class_name, self.role,
        )
        if not saved:
            text = "Your character choices changed or your entry was removed. Open Edit again."
        else:
            try:
                await self.cog.refresh(self.channel_id)
                text = "Saved. Ogre updated your character choices."
            except Exception as exc:
                print(f"LFG CHARACTER REFRESH FAILED channel={self.channel_id} error={exc!r}")
                text = "Changes saved, but board refresh failed. Tell an officer."
        await interaction.edit_original_response(content=text, view=None)
        self.stop()

    async def cancel(self, interaction):
        await interaction.response.edit_message(content="Cancelled. Nothing changed.", view=None)
        self.stop()


class CharacterEditButton(discord.ui.Button):
    def __init__(self, cog, channel_id, field):
        super().__init__(
            label=f"Edit {field.title()}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"hyped_lfg_edit_{field}_v1",
        )
        self.cog = cog
        self.channel_id = channel_id
        self.field = field

    async def callback(self, interaction):
        row = current_listing(self.channel_id, interaction.user.id)
        if row is None:
            await interaction.response.send_message(
                "Add your plan first using Add / Update.", ephemeral=True)
            return
        view = CharacterEditView(
            self.cog, self.channel_id, interaction.user.id, row, self.field)
        await interaction.response.send_message(
            content=view.summary(), view=view, ephemeral=True)


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
        await interaction.response.defer(thinking=True, ephemeral=True)
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

        await interaction.followup.send(text, ephemeral=True)


class BoardPageButton(discord.ui.Button):
    """Navigate among V2 containers by editing the *existing* board message."""

    def __init__(self, delta, page_index, total_pages):
        label = "‹ Previous" if delta == -1 else "Next ›"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=("hyped_lfg_page_prev_v1" if delta == -1
                       else "hyped_lfg_page_next_v1"),
            disabled=(page_index == 0 if delta == -1
                      else page_index >= total_pages - 1),
        )
        self.delta = delta

    async def callback(self, interaction):
        layout = self.view
        if layout.preview:
            roster = demo_entries()
            page = clamp_page(layout.page_index + self.delta, len(roster))
            await interaction.response.edit_message(
                view=BoardLayout(layout.cog, layout.channel_id,
                                 interaction.guild, preview=True, page_index=page),
            )
            return

        # Page changes do not insert/delete messages or touch member records.
        async with layout.cog.lock:
            roster = entries(layout.channel_id)
            page = clamp_page(layout.page_index + self.delta, len(roster))
            await interaction.response.edit_message(
                view=BoardLayout(layout.cog, layout.channel_id,
                                 interaction.guild, page_index=page,
                                 roster=roster),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            with connect() as db:
                db.execute(
                    "UPDATE lfg_boards SET page_index=? WHERE channel_id=?",
                    (page, layout.channel_id),
                )


class BoardLayout(discord.ui.LayoutView):
    """One Discord message with a V2 container for each visible member."""

    def __init__(self, cog, channel_id, guild=None, *, preview=False,
                 page_index=0, roster=None):
        super().__init__(timeout=900 if preview else None)
        self.cog = cog
        self.channel_id = channel_id
        self.preview = preview

        # Preview never reads/writes real member records.
        if roster is None:
            roster = demo_entries() if preview else entries(channel_id)
        self.page_index = clamp_page(page_index, len(roster))
        total_pages = page_count(len(roster))

        heading = discord.ui.Section(
            discord.ui.TextDisplay(
                "## <Hyped> LFG-LFM Board\n"
                "**Ogre want know launch plans.**"
                + ("\n-# DEMO PREVIEW · 20 fictional players · nothing saved"
                   if preview else "")
            ),
            accessory=discord.ui.Thumbnail(
                HEADER_IMAGE, description="Hyped LFG-LFM",
            ),
        )
        self.add_item(discord.ui.Container(heading, accent_color=ACCENT))

        if preview:
            # The preview is navigable but cannot edit real member data.
            self.add_item(discord.ui.ActionRow(
                *[discord.ui.Button(
                    label=label, style=discord.ButtonStyle.secondary,
                    disabled=True,
                ) for label in ("Add / Update", "Edit Race", "Edit Class", "Remove")]
            ))
        else:
            self.add_item(discord.ui.ActionRow(
                AddUpdateButton(cog, channel_id),
                CharacterEditButton(cog, channel_id, "race"),
                CharacterEditButton(cog, channel_id, "class"),
                RemoveButton(cog, channel_id),
            ))

        if total_pages > 1:
            self.add_item(discord.ui.ActionRow(
                BoardPageButton(-1, self.page_index, total_pages),
                BoardPageButton(+1, self.page_index, total_pages),
            ))
            self.add_item(discord.ui.TextDisplay(
                f"-# Page {self.page_index + 1} of {total_pages} · "
                f"{len(roster)} member plan(s)"
            ))

        visible = page_rows(roster, self.page_index)
        for row in visible:
            race = row["race"] or "Race not set"
            zone = row["starting_zone"] or STARTING_ZONES.get(race, "Not set")
            roles = format_roles(row["role"], icons=True) or "Role not set"
            days = row["days_off"] or "Not set"
            character = safe(row["character"]) if row["character"] else "Name TBD"
            notes = safe(row["notes"]) if row["notes"] else ""
            open_marker = (
                f"{OPEN_LFM_MARKER} " if show_lfm_emoji(row["activity"]) else ""
            )

            lines = [
                f"**{character} · {display_race(race)} {row['class_name']} · {roles}**",
            ]
            if not is_solo_or_sorted(row["activity"]):
                lines.append(
                    f"-# **Playing:** "
                    f"{safe(display_uk_times(compact_availability(row['availability'])))} "
                    f"· {safe(compact_days(days))}"
                )
            lines.append(f"-# **Plan:** {open_marker}{safe(row['activity'])}")
            if notes:
                lines.append(f"-# **Notes:** {notes}")
            lines.append(contact_line(row, preview=preview))

            # Separate V2 containers are still part of this ONE message.
            container = discord.ui.Container(accent_color=ACCENT)
            container.add_item(discord.ui.Section(
                discord.ui.TextDisplay("\n".join(lines)),
                accessory=discord.ui.Thumbnail(
                    player_avatar_url(row, guild) or HEADER_IMAGE,
                    description=f"{character} Discord avatar",
                ),
            ))
            # Containers separate players visually; no extra horizontal rule.
            self.add_item(container)

        if not visible:
            self.add_item(discord.ui.TextDisplay("*Nobody on board yet.*"))

        # Always the very bottom of the *last* page, not a new message.
        if self.page_index == total_pages - 1:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=BOTTOM_IMAGE, description="Hyped")
            self.add_item(gallery)


class LFGPreview(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    async def cog_load(self):
        init_db()
        with connect() as db:
            boards = db.execute(
                "SELECT channel_id, guild_id, message_id, page_index FROM lfg_boards"
            ).fetchall()
        for board in boards:
            self.bot.add_view(
                BoardLayout(
                    self,
                    board["channel_id"],
                    self.bot.get_guild(board["guild_id"]),
                    page_index=board["page_index"],
                ),
                message_id=board["message_id"],
            )

    async def refresh(self, channel_id):
        """Update the original message only; never create public player posts."""
        async with self.lock:
            row = get_board(channel_id)
            if not row:
                raise RuntimeError("No stored LFG board for this channel")

            channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
            guild = getattr(channel, "guild", None)
            roster = entries(channel_id)
            page = clamp_page(row["page_index"], len(roster))

            anchor = channel.get_partial_message(row["message_id"])
            await anchor.edit(
                content=None, embeds=[], attachments=[],
                view=BoardLayout(self, channel_id, guild,
                                 page_index=page, roster=roster),
                allowed_mentions=discord.AllowedMentions.none(),
            )

            with connect() as db:
                db.execute(
                    "UPDATE lfg_boards SET page_index=? WHERE channel_id=?",
                    (page, channel_id),
                )
                # Old code generated dozens of follow-up player messages.
                # Remove only IDs tracked in its own isolated table.
                legacy_parts = [
                    dict(r) for r in db.execute(
                        "SELECT position,message_id FROM lfg_board_parts "
                        "WHERE channel_id=? ORDER BY position",
                        (channel_id,),
                    )
                ]
            for part in legacy_parts:
                try:
                    await channel.get_partial_message(part["message_id"]).delete()
                except discord.NotFound:
                    pass  # Already deleted; safe to remove its tracking row.
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(
                        f"LFG OLD PART CLEANUP FAILED channel={channel_id} "
                        f"message={part['message_id']} error={exc!r}"
                    )
                    continue  # Keep its ID so the next refresh can retry.
                with connect() as db:
                    db.execute(
                        "DELETE FROM lfg_board_parts WHERE channel_id=? AND message_id=?",
                        (channel_id, part["message_id"]),
                    )

    async def post_new_board(self, interaction, channel):
        """Recreate a missing public board without deleting any saved plans.

        A stale message ID (including an obsolete ephemeral test response)
        must not block launch. The LFG listing table is never touched here.
        """
        try:
            # channel.send is always a PUBLIC server message, unlike the
            # command's private acknowledgement or /lfg-preview.
            # Reuse the stored page when restoring a missing message.
            existing = get_board(channel.id)
            page = existing["page_index"] if existing else 0
            msg = await channel.send(
                view=BoardLayout(self, channel.id, interaction.guild,
                                 page_index=page),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            with connect() as db:
                db.execute(
                    "INSERT INTO lfg_boards(channel_id,guild_id,message_id,board_day,page_index) "
                    "VALUES(?,?,?,'launch',?) "
                    "ON CONFLICT(channel_id) DO UPDATE SET "
                    "message_id=excluded.message_id,"
                    "guild_id=excluded.guild_id,"
                    "board_day='launch',"
                    "page_index=excluded.page_index",
                    (channel.id, interaction.guild_id, msg.id, page),
                )
            # Old multi-message versions may have posted individual entries.
            # Clean up only those explicitly tracked messages.
            await self.refresh(channel.id)
            await interaction.followup.send(
                f"✅ Public LFG-LFM board ready: {msg.jump_url}",
                ephemeral=True,
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"LFG BOARD POST FAILED channel={channel.id} error={exc!r}")
            await interaction.followup.send(
                "Ogre couldn't publish the public board. Check that the bot "
                "can Send Messages, Embed Links and use external images in "
                "this channel, then check Fly logs.",
                ephemeral=True,
            )
        except Exception as exc:
            print(f"LFG BOARD POST FAILED channel={channel.id} error={exc!r}")
            await interaction.followup.send(
                "Ogre couldn't finish setting up the public board. "
                "Saved member plans are untouched; check Fly logs.",
                ephemeral=True,
            )

    @app_commands.command(
        name="postlfglfm",
        description="Post or restore the public Hyped LFG-LFM launch board",
    )
    @app_commands.guilds(discord.Object(id=int(os.environ["HYPED_GUILD_ID"])))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def post_lfg_lfm(self, interaction: discord.Interaction):
        if interaction.guild_id != int(os.environ["HYPED_GUILD_ID"]):
            await interaction.response.send_message(
                "Only available in Hyped.", ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use this in a server text channel.", ephemeral=True,
            )
            return

        # Acknowledge immediately; public message is posted via channel.send.
        # This PRIVATE status response does not make the board ephemeral.
        await interaction.response.defer(thinking=True, ephemeral=True)
        row = get_board(channel.id)
        if row:
            try:
                existing = await channel.fetch_message(row["message_id"])
            except discord.NotFound:
                # Only the board message is gone, NOT member listings.
                print(
                    f"LFG BOARD MISSING channel={channel.id} "
                    f"message={row['message_id']}; restoring public board"
                )
                await self.post_new_board(interaction, channel)
                return
            except (discord.Forbidden, discord.HTTPException) as exc:
                # A temporary API problem or missing permission is not proof
                # that the board was deleted: avoid duplicating public boards.
                print(
                    f"LFG BOARD LOOKUP FAILED channel={channel.id} "
                    f"message={row['message_id']} error={exc!r}"
                )
                await interaction.followup.send(
                    "Couldn't check the existing public board. It may still be "
                    "there; check bot permissions and Fly logs. No new board "
                    "was posted, and saved plans were not changed.",
                    ephemeral=True,
                )
                return
            try:
                await self.refresh(channel.id)
            except discord.NotFound:
                # The anchor can disappear between fetch and edit.
                await self.post_new_board(interaction, channel)
                return
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(
                    f"LFG BOARD REFRESH FAILED channel={channel.id} "
                    f"message={row['message_id']} error={exc!r}"
                )
                await interaction.followup.send(
                    "Ogre found the board but couldn't refresh it. Saved plans "
                    "are safe; check the bot's channel permissions and Fly logs.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"✅ Public LFG-LFM board refreshed: {existing.jump_url}",
                ephemeral=True,
            )
            return

        await self.post_new_board(interaction, channel)

    @app_commands.command(
        name="lfg-preview",
        description="Preview 20 dummy plans in one navigable V2 board message",
    )
    @app_commands.guilds(discord.Object(id=int(os.environ["HYPED_GUILD_ID"])))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def lfg_preview(self, interaction: discord.Interaction):
        if interaction.guild_id != int(os.environ["HYPED_GUILD_ID"]):
            await interaction.response.send_message(
                "Only available in Hyped.", ephemeral=True,
            )
            return

        # Private preview: one editable ephemeral message; no live data.
        await interaction.response.send_message(
            view=BoardLayout(self, interaction.channel_id,
                             interaction.guild, preview=True),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(LFGPreview(bot))
