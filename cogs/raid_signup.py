"""Private raid management: local database and optional Google Sheets sync."""
import os
import asyncio
import io
import logging
import sqlite3
import time
from urllib.parse import urlsplit
from datetime import datetime, timezone
from contextlib import contextmanager

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageOps, UnidentifiedImageError

from cogs import raid_sheets, raid_sheets_v3
from cogs.emojis import markup as guild_emoji, option_emoji, EMOJI_IDS
from cogs.raid_poster import RaidPosterView
from cogs.raid_schedule import (
    RaidEventPanel, active_panels, ensure_raid_fields, latest_raid, list_raids,
    read_raid, read_public_roster, record_panel, save_raid, set_softres_url, delete_raid,
    raid_status,
)

GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
TEST_CHANNEL_ID = 1564594901746716713
RAID_CHANNEL_ID = 1563663151323553802
RAID_DB = os.getenv("RAID_DB_PATH", "/data/raid_signup.db" if os.path.isdir("/data") else "raid_signup.db")
ACCENT = 0xA87535
LOGGER = logging.getLogger(__name__)

SHEET_TABS = {
    "Characters": (
        "Character ID", "Discord ID", "Discord Name", "Character", "Class",
        "Spec", "Role", "Main / Alt", "Profile Updated", "SimC Updated",
    ),
    "Sign-ups": (
        "Raid ID", "Raid Date", "Discord ID", "Character ID", "Character",
        "Availability", "Sign-up Notes", "Sign-up Time", "Last Updated",
        "Signed off at",
    ),
    "Gear Checks": (
        "Character ID", "Discord ID", "Character", "Latest Gear Upload",
        "Export Format", "Item Summary", "Gear Review", "Leadership Notes",
        "Raw Export Reference",
    ),
    "Roster Builder": (
        "Raid ID", "Raid Date", "Discord ID", "Character ID", "Character",
        "Class", "Spec", "Assigned Role", "Selection", "Bench Notes",
        "Show on Discord", "Ony (40m)", "Hyjal (20m)", "Barrows (10m)",
        "Player Source",
    ),
    "Attendance": (
        "Raid ID", "Raid Date", "Discord ID", "Discord Name", "Character ID",
        "Character", "Roster Status", "Attendance", "Late Minutes",
        "AFK Minutes", "Excused", "Leadership Notes", "Updated At",
    ),
    "Raid History": (
        "Raid ID", "Raid Date", "Instance", "Start Time",
        "Roster Published", "Raid Completed", "Leadership Notes",
    ),
}

ATTENDANCE_OPTIONS = ("Pending", "Attended", "Late", "AFK", "No-show", "Excused")


@contextmanager
def database(path=RAID_DB):
    con = sqlite3.connect(path, timeout=20)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        with con:
            yield con
    finally:
        con.close()


def create_schema(path=RAID_DB):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with database(path) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS characters(
            character_id INTEGER PRIMARY KEY,
            discord_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            realm TEXT, class_name TEXT, spec TEXT, role TEXT,
            is_main INTEGER NOT NULL DEFAULT 1,
            last_simc_at INTEGER,
            UNIQUE(discord_id, name, realm)
        );
        CREATE TABLE IF NOT EXISTS raids(
            raid_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            raid_at TEXT NOT NULL,
            custom_title TEXT,
            banner_url TEXT,
            roster_posted_at TEXT,
            roster_updated_at TEXT,
            signup_open INTEGER NOT NULL DEFAULT 0,
            roster_published INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS signups(
            raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
            discord_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL REFERENCES characters(character_id),
            availability TEXT NOT NULL DEFAULT 'Available',
            note TEXT,
            submitted_at TEXT NOT NULL,
            PRIMARY KEY(raid_id, discord_id)
        );
        CREATE TABLE IF NOT EXISTS raid_absences(
            raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
            discord_id INTEGER NOT NULL,
            note TEXT,
            signed_off_at TEXT NOT NULL,
            PRIMARY KEY(raid_id, discord_id)
        );
        CREATE TABLE IF NOT EXISTS raid_public_roster(
            raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            assignment TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (raid_id, position)
        );
        CREATE TABLE IF NOT EXISTS gear_exports(
            export_id INTEGER PRIMARY KEY,
            character_id INTEGER NOT NULL REFERENCES characters(character_id),
            submitted_at TEXT NOT NULL,
            format TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            parsed_json TEXT
        );
        CREATE TABLE IF NOT EXISTS gear_images(
            image_id INTEGER PRIMARY KEY,
            character_id INTEGER NOT NULL REFERENCES characters(character_id),
            submitted_at TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            image_data BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance(
            raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
            discord_id INTEGER NOT NULL,
            character_id INTEGER REFERENCES characters(character_id),
            status TEXT NOT NULL DEFAULT 'Pending'
                CHECK(status IN ('Pending', 'Attended', 'Late', 'AFK', 'No-show', 'Excused')),
            late_minutes INTEGER,
            afk_minutes INTEGER,
            leadership_notes TEXT,
            updated_at TEXT,
            PRIMARY KEY(raid_id, discord_id)
        );
        """)

        # Historic registrations allowed separate realm/casing variants of
        # one character. Consolidate those on startup without losing any
        # sign-ups, attendance references or saved SimC snapshots.
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT character_id, discord_id, name, is_main, last_simc_at "
            "FROM characters ORDER BY character_id"
        ).fetchall()
        grouped = {}
        for character_id, member_id, character_name, is_main, simc_at in rows:
            normalized = " ".join((character_name or "").split())
            key = (member_id, normalized.casefold())
            grouped.setdefault(key, []).append(
                (character_id, normalized, is_main, simc_at)
            )
        for matches in grouped.values():
            keep_id, clean_name, _, _ = matches[0]
            for duplicate_id, _, _, _ in matches[1:]:
                for table in ("signups", "attendance", "gear_exports", "gear_images"):
                    con.execute(
                        f"UPDATE {table} SET character_id=? WHERE character_id=?",
                        (keep_id, duplicate_id),
                    )
                con.execute(
                    "DELETE FROM characters WHERE character_id=?", (duplicate_id,)
                )
            con.execute(
                "UPDATE characters SET name=?, is_main=?, last_simc_at=? "
                "WHERE character_id=?",
                (clean_name, max(row[2] for row in matches),
                 max((row[3] or 0) for row in matches) or None, keep_id),
            )
        # One identity per Discord user and character name, ignoring case
        # and the legacy realm column. Different characters remain separate.
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_raid_characters_member_name "
            "ON characters(discord_id, name COLLATE NOCASE)"
        )


# Forever launches with nine classic classes. Each spec has one or more raid roles.
# The guild shares the PvP ruleset; no realm or ruleset field is needed.
CLASS_SPECS = {
    "Druid": {"Balance": ("DPS",), "Feral": ("Tank", "DPS"),
              "Restoration": ("Healer",)},
    "Hunter": {"Beast Mastery": ("DPS",), "Marksmanship": ("DPS",),
               "Survival": ("DPS",)},
    "Mage": {"Arcane": ("DPS",), "Fire": ("DPS",), "Frost": ("DPS",)},
    "Paladin": {"Holy": ("Healer",), "Protection": ("Tank",),
                "Retribution": ("DPS",)},
    "Priest": {"Discipline": ("Healer",), "Holy": ("Healer",),
               "Shadow": ("DPS",)},
    "Rogue": {"Assassination": ("DPS",), "Combat": ("DPS",),
              "Subtlety": ("DPS",)},
    "Shaman": {"Elemental": ("DPS",), "Enhancement": ("DPS",),
               "Restoration": ("Healer",)},
    "Warlock": {"Affliction": ("DPS",), "Demonology": ("DPS",),
                "Destruction": ("DPS",)},
    "Warrior": {"Arms": ("DPS",), "Fury": ("DPS",),
                "Protection": ("Tank",)},
}


def is_test_owner(interaction):
    return (interaction.user.id == OWNER_ID and interaction.guild_id == GUILD_ID
            and interaction.channel_id == TEST_CHANNEL_ID)

def is_test_member(interaction):
    """Members can use the real raid panel, but only inside private #ogre."""
    return (interaction.guild_id == GUILD_ID
            and interaction.channel_id == TEST_CHANNEL_ID)


def save_character(discord_id, name, class_name, spec, role, path=RAID_DB):
    """Update by player + character name, including records from the old realm form.

    Keep the legacy realm column in SQLite for migration compatibility, but never
    request or display it. Never modify a profile belonging to another Discord ID.
    """
    name = " ".join(name.split())
    if not name or len(name) > 40:
        raise ValueError("Enter a character name (40 characters maximum).")
    if role not in CLASS_SPECS.get(class_name, {}).get(spec, ()):
        raise ValueError("Choose a valid class, spec and raid role.")
    with database(path) as con:
        # Hold a write lock across lookup+insert to prevent competing clicks
        # registering the same character twice.
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute(
            "SELECT character_id FROM characters "
            "WHERE discord_id=? AND name=? COLLATE NOCASE "
            "ORDER BY character_id LIMIT 1",
            (discord_id, name),
        ).fetchone()
        if previous:
            con.execute(
                "UPDATE characters SET class_name=?, spec=?, role=? "
                "WHERE character_id=? AND discord_id=?",
                (class_name, spec, role, previous[0], discord_id),
            )
            return "updated"
        con.execute(
            "INSERT INTO characters "
            "(discord_id, name, realm, class_name, spec, role) "
            "VALUES (?, ?, '', ?, ?, ?)",
            (discord_id, name, class_name, spec, role),
        )
        return "created"


def list_characters(discord_id, path=RAID_DB):
    with database(path) as con:
        return con.execute(
            "SELECT name, class_name, spec, role FROM characters "
            "WHERE discord_id=? ORDER BY is_main DESC, name",
            (discord_id,),
        ).fetchall()


def character_list_text(discord_id):
    rows = list_characters(discord_id)
    lines = [
        f"**{discord.utils.escape_markdown(name)}** · {class_name} / {spec} · **{role}**"
        for name, class_name, spec, role in rows
    ]
    return (
        "## My characters\n"
        + ("\n".join(lines) if lines else "Ogre not know any characters yet.")
        + "\n\n-# Update an existing character using /raid register and the same name."
    )


def member_characters(discord_id, path=RAID_DB):
    with database(path) as con:
        return con.execute(
            "SELECT character_id, name, class_name, spec, role, last_simc_at "
            "FROM characters WHERE discord_id=? ORDER BY is_main DESC, name",
            (discord_id,),
        ).fetchall()


def save_gear(discord_id, character_id, text, path=RAID_DB):
    """Store a reusable raw SimC export independent of any raid application."""
    text = text.strip()
    if len(text) < 50 or len(text) > 200000:
        raise ValueError("Upload a complete SimC export (50–200,000 characters).")
    if not ("player=" in text.lower() or "level=" in text.lower()
            or "simulationcraft" in text.lower()):
        raise ValueError("Ogre need a SimulationCraft character export, not a description.")
    now = datetime.now(timezone.utc).isoformat()
    with database(path) as con:
        character = con.execute(
            "SELECT character_id FROM characters WHERE character_id=? AND discord_id=?",
            (character_id, discord_id),
        ).fetchone()
        if not character:
            raise ValueError("Ogre not know that character belongs to you.")
        con.execute(
            "INSERT INTO gear_exports (character_id, submitted_at, format, raw_text) "
            "VALUES (?, ?, 'simc_raw', ?)",
            (character_id, now, text),
        )
        con.execute(
            "UPDATE characters SET last_simc_at=? WHERE character_id=?",
            (int(time.time()), character_id),
        )


def update_character_profile(discord_id, character_id, class_name, spec, role, path=RAID_DB):
    """Edit class/spec/role in place, retaining gear exports and all raid signups."""
    if role not in CLASS_SPECS.get(class_name, {}).get(spec, ()):
        raise ValueError("Choose a valid class, spec and raid role.")
    with database(path) as con:
        row = con.execute(
            "SELECT name FROM characters WHERE character_id=? AND discord_id=?",
            (character_id, discord_id),
        ).fetchone()
        if not row:
            raise ValueError("Ogre cannot edit someone else's character.")
        con.execute(
            "UPDATE characters SET class_name=?, spec=?, role=? "
            "WHERE character_id=? AND discord_id=?",
            (class_name, spec, role, character_id, discord_id),
        )
    return row[0]


def save_gear_image(discord_id, character_id, filename, payload, path=RAID_DB):
    """Validate/compress a screenshot into the isolated raid database, no URLs."""
    if len(payload) > 8 * 1024 * 1024:
        raise ValueError("Screenshot too big (8 MB max).")
    if len(payload) < 100:
        raise ValueError("Screenshot file is empty or invalid.")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError("Upload PNG, JPG or WEBP character-panel screenshots.")
            if image.width * image.height > 24_000_000:
                raise ValueError("Screenshot resolution is too large.")
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1800, 1800))
            data = io.BytesIO()
            image.convert("RGB").save(data, format="JPEG", quality=82, optimize=True)
            screenshot = data.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("That is not a valid character-panel screenshot.") from exc
    with database(path) as con:
        row = con.execute(
            "SELECT 1 FROM characters WHERE character_id=? AND discord_id=?",
            (character_id, discord_id),
        ).fetchone()
        if not row:
            raise ValueError("That character doesn't belong to you.")
        con.execute(
            "INSERT INTO gear_images "
            "(character_id, submitted_at, filename, mime_type, image_data) "
            "VALUES (?, ?, ?, 'image/jpeg', ?)",
            (character_id, datetime.now(timezone.utc).isoformat(),
             "character-panel.jpg", screenshot),
        )


def last_gear_image(character_id, path=RAID_DB):
    with database(path) as con:
        return con.execute(
            "SELECT filename, image_data FROM gear_images "
            "WHERE character_id=? ORDER BY image_id DESC LIMIT 1",
            (character_id,),
        ).fetchone()


def save_signup(raid_id, discord_id, character_id, path=RAID_DB, *, note=None):
    """Accept one signed-up character per member/night with optional note."""
    if note is not None:
        note = note.strip()
        if len(note) > 500:
            raise ValueError("Sign-up note must be 500 characters or less.")
    with database(path) as con:
        event = con.execute(
            "SELECT signup_open, roster_published FROM raids WHERE raid_id=?",
            (raid_id,),
        ).fetchone()
        if not event or not event[0] or event[1]:
            raise ValueError("Sign-ups aren't open for this raid.")
        character = con.execute(
            "SELECT character_id FROM characters "
            "WHERE character_id=? AND discord_id=?",
            (character_id, discord_id),
        ).fetchone()
        if not character:
            raise ValueError("Choose one of your own saved characters.")
        con.execute("""
            INSERT INTO signups(raid_id, discord_id, character_id, availability, note, submitted_at)
            VALUES (?, ?, ?, 'Available', ?, ?)
            ON CONFLICT(raid_id, discord_id) DO UPDATE SET
                character_id=excluded.character_id, availability='Available',
                note=CASE
                    WHEN ? IS NULL AND signups.availability='Available'
                    THEN signups.note
                    WHEN ? IS NULL THEN NULL
                    ELSE excluded.note END,
                submitted_at=CASE
                    WHEN signups.character_id=excluded.character_id
                     AND signups.availability='Available'
                    THEN signups.submitted_at
                    ELSE excluded.submitted_at END
        """, (raid_id, discord_id, character_id, note, datetime.now(timezone.utc).isoformat(), note, note))
        # Returning after an absence restores the player's Available status.
        con.execute(
            "DELETE FROM raid_absences WHERE raid_id=? AND discord_id=?",
            (raid_id, discord_id),
        )


def my_signup(raid_id, discord_id, path=RAID_DB):
    with database(path) as con:
        return con.execute(
            "SELECT c.name, s.availability FROM signups s "
            "JOIN characters c ON c.character_id=s.character_id "
            "WHERE s.raid_id=? AND s.discord_id=?",
            (raid_id, discord_id),
        ).fetchone()


def save_absence(raid_id, discord_id, note="", path=RAID_DB):
    """Record the time someone marked themselves absent, with an optional note.

    Absence never requires a character; keep prior signed-up characters linked
    as Unavailable so leadership can still see the original application.
    """
    note = (note or "").strip()
    if len(note) > 500:
        raise ValueError("Note must be 500 characters or less.")
    timestamp = datetime.now(timezone.utc).isoformat()
    with database(path) as con:
        event = con.execute(
            "SELECT raid_id, archived_at FROM raids WHERE raid_id=?", (raid_id,),
        ).fetchone()
        if not event:
            raise ValueError("Raid no longer exists.")
        if event[1]:
            raise ValueError("Archived raids are read-only.")
        con.execute("""
            INSERT INTO raid_absences(raid_id, discord_id, note, signed_off_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(raid_id, discord_id) DO UPDATE SET
                note=excluded.note, signed_off_at=excluded.signed_off_at
        """, (raid_id, discord_id, note or None, timestamp))
        # Keep the original sign-up note separate from absence notes.
        con.execute(
            "UPDATE signups SET availability='Unavailable' "
            "WHERE raid_id=? AND discord_id=?",
            (raid_id, discord_id),
        )
    return timestamp


def my_absence(raid_id, discord_id, path=RAID_DB):
    with database(path) as con:
        return con.execute(
            "SELECT note, signed_off_at FROM raid_absences "
            "WHERE raid_id=? AND discord_id=?",
            (raid_id, discord_id),
        ).fetchone()


class AbsenceNoteModal(discord.ui.Modal):
    """Note optional; signed-off time comes from the server, not user input."""
    def __init__(self, owner_id, raid_id):
        super().__init__(title=f"Absence · Raid #{raid_id}")
        self.owner_id = owner_id
        self.raid_id = raid_id
        self.note = discord.ui.TextInput(
            label="Note (optional)", placeholder="Anything Ogre should know?",
            required=False, style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction):
        if not is_test_member(interaction) or interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This absence form belongs to another player.", ephemeral=True
            )
            return
        try:
            signed_off = save_absence(
                self.raid_id, self.owner_id, self.note.value
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't record absence: {exc}", ephemeral=True
            )
            return
        unix = int(datetime.fromisoformat(signed_off).timestamp())
        await interaction.response.send_message(
            f"Ogre mark you absent for Raid #{self.raid_id}. "
            f"Signed off <t:{unix}:F>.", ephemeral=True,
        )


class SignupNoteModal(discord.ui.Modal):
    """One sign-up for the whole raid night, including an optional note."""

    def __init__(self, member_id, raid_id, char_id, name):
        super().__init__(title=f"Sign up · Raid #{raid_id}")
        self.member_id = member_id
        self.raid_id = raid_id
        self.char_id = char_id
        self.name = name
        self.note = discord.ui.TextInput(
            label="Note (optional)", placeholder="Any plans Ogre should know?",
            required=False, style=discord.TextStyle.paragraph, max_length=500,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction):
        if not is_test_member(interaction) or interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "That sign-up isn't yours.", ephemeral=True
            )
            return
        try:
            existing = my_signup(self.raid_id, self.member_id)
            save_signup(self.raid_id, self.member_id, self.char_id,
                        note=self.note.value)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't sign you up: {exc}", ephemeral=True
            )
            return
        already = existing and existing[0].casefold() == self.name.casefold() \
            and existing[1] == "Available"
        await interaction.response.send_message(
            f"Ogre {'updated' if already else 'saved'} "
            f"**{discord.utils.escape_markdown(self.name)}** for Raid "
            f"#{self.raid_id}.", ephemeral=True,
        )


class GearUploadModal(discord.ui.Modal):
    """SimC text, SimC file, character-panel screenshot, or both together."""

    def __init__(self, member_id, char_id, char_name):
        super().__init__(title="Gear · " + char_name[:32])
        self.member_id = member_id
        self.char_id = char_id
        self.char_name = char_name
        self.simc = discord.ui.TextInput(
            label="SimC paste (optional)", required=False,
            style=discord.TextStyle.paragraph, max_length=4000,
        )
        self.uploads = discord.ui.FileUpload(
            required=False, min_values=0, max_values=2,
        )
        self.add_item(self.simc)
        self.add_item(discord.ui.Label(
            text="SimC file or character screenshot",
            description="Optional .txt/.simc, PNG, JPG or WEBP (up to 2 files)",
            component=self.uploads,
        ))

    async def on_submit(self, interaction):
        if not is_test_member(interaction) or interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "This character belongs to another player.", ephemeral=True
            )
            return
        simc_text = (self.simc.value or "").strip()
        attachments = list(self.uploads.values or [])
        if not simc_text and not attachments:
            await interaction.response.send_message(
                "Ogre need SimC text or a character-panel screenshot.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        saved = []
        try:
            if simc_text:
                save_gear(self.member_id, self.char_id, simc_text)
                saved.append("SimC")
            for attachment in attachments:
                filename = attachment.filename.lower()
                if filename.endswith((".txt", ".simc")):
                    if attachment.size > 200_000:
                        raise ValueError("SimC file must be under 200 KB.")
                    data = await attachment.read()
                    if len(data) > 200_000:
                        raise ValueError("SimC file must be under 200 KB.")
                    save_gear(
                        self.member_id, self.char_id, data.decode("utf-8-sig")
                    )
                    saved.append("SimC")
                elif filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    if attachment.size > 8 * 1024 * 1024:
                        raise ValueError("Screenshot must be under 8 MB.")
                    data = await attachment.read()
                    save_gear_image(
                        self.member_id, self.char_id, filename, data
                    )
                    saved.append("screenshot")
                else:
                    raise ValueError("Use a .txt/.simc or PNG/JPG/WEBP file.")
        except (ValueError, UnicodeError, sqlite3.Error, discord.HTTPException) as exc:
            await interaction.followup.send(
                f"Ogre couldn't save all gear: {exc}", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Ogre saved **{discord.utils.escape_markdown(self.char_name)}** gear: "
            f"{', '.join(saved)}.", ephemeral=True
        )


class CharacterActionView(discord.ui.LayoutView):
    """Private player choice before sign-up, edit or gear upload."""

    def __init__(self, owner_id, *, raid_id=None, purpose="signup", current_signup=None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.raid_id = raid_id
        self.purpose = purpose
        rows = member_characters(owner_id)
        titles = {"signup": "Sign up", "gear": "Gear", "update": "Update"}
        hint = {
            "signup": "Pick character. Ogre save your raid-night note.",
            "gear": "Pick character. Add SimC, panel screenshot, or both.",
            "update": "Pick character. Ogre change class, spec or role.",
        }[purpose]
        if purpose == "signup" and current_signup:
            hint = (
                f"**Now:** {discord.utils.escape_markdown(current_signup[0])}"
                f" · {current_signup[1]}\n" + hint
            )
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"## {titles[purpose]}\n{hint}"),
            accent_color=ACCENT,
        ))
        if not rows:
            return
        selection = discord.ui.Select(
            placeholder="Choose your saved character",
            options=[
                discord.SelectOption(
                    label=name[:100], value=str(char_id),
                    description=f"{class_name} / {spec} · {role}"[:100],
                    emoji=option_emoji(class_name),
                ) for char_id, name, class_name, spec, role, _ in rows[:25]
            ],
        )

        async def select_character(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True
                )
                return
            char_id = int(selection.values[0])
            match = next((r for r in rows if r[0] == char_id), None)
            if not match:
                await interaction.response.send_message(
                    "Character list expired. Reopen from the raid panel.",
                    ephemeral=True,
                )
                return
            if self.purpose == "gear":
                await interaction.response.send_modal(
                    GearUploadModal(self.owner_id, char_id, match[1])
                )
            elif self.purpose == "update":
                await interaction.response.edit_message(
                    view=ClassPickerView(
                        self.owner_id, editing_character_id=char_id,
                    )
                )
            else:
                await interaction.response.send_modal(
                    SignupNoteModal(self.owner_id, self.raid_id, char_id, match[1])
                )

        selection.callback = select_character
        self.add_item(discord.ui.ActionRow(selection))


class CharacterNameModal(discord.ui.Modal):
    def __init__(self, owner_id, class_name, spec, role, signup_raid_id=None):
        super().__init__(title="Save character")
        self.owner_id = owner_id
        self.class_name = class_name
        self.spec = spec
        self.role = role
        self.signup_raid_id = signup_raid_id
        self.name = discord.ui.TextInput(
            label="Character name", placeholder="Firstname Lastname", max_length=40
        )
        self.add_item(self.name)
        if signup_raid_id is not None:
            self.note = discord.ui.TextInput(
                label="Raid note (optional)", required=False,
                style=discord.TextStyle.paragraph, max_length=500,
                placeholder="Any plans Ogre should know?",
            )
            self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_test_member(interaction) or interaction.user.id != self.owner_id:
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        try:
            outcome = save_character(interaction.user.id, self.name.value,
                                     self.class_name, self.spec, self.role)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't save character: {exc}", ephemeral=True
            )
            return
        name = discord.utils.escape_markdown(self.name.value.strip())
        if self.signup_raid_id is not None:
            try:
                character = next(
                    row for row in member_characters(interaction.user.id)
                    if row[1].casefold() == self.name.value.strip().casefold()
                )
                save_signup(
                    self.signup_raid_id, interaction.user.id, character[0],
                    note=self.note.value,
                )
            except (StopIteration, ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(
                    f"Saved **{name}**, but couldn't sign up for Raid "
                    f"#{self.signup_raid_id}: {exc}. Press Sign up to retry.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"**{name}** registered and signed up for Raid "
                f"#{self.signup_raid_id}! ✅\n"
                "Nobody else can see your sign-up. "
                "You can update SimC gear separately.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Ogre {'updated' if outcome == 'updated' else 'saved'} "
            f"**{name}** · {self.class_name} / {self.spec} · "
            f"**{self.role}**.\nUse **/raid characters** to see saved profiles.",
            ephemeral=True,
        )


class ClassPickerView(discord.ui.LayoutView):
    def __init__(self, owner_id, signup_raid_id=None, editing_character_id=None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.signup_raid_id = signup_raid_id
        self.editing_character_id = editing_character_id
        title = (f"## Sign up · Raid #{signup_raid_id}\n"
                 if signup_raid_id is not None else
                 "## Update character\n" if editing_character_id is not None
                 else "## Register character\n")
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                title + "Choose class, then spec and role."
            ), accent_color=ACCENT,
        ))
        select = discord.ui.Select(
            placeholder="Choose your class",
            options=[discord.SelectOption(label=name, value=name, emoji=option_emoji(name))
                     for name in CLASS_SPECS],
        )

        async def select_class(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=SpecPickerView(
                    self.owner_id, select.values[0],
                    signup_raid_id=self.signup_raid_id,
                    editing_character_id=self.editing_character_id,
                )
            )

        select.callback = select_class
        self.add_item(discord.ui.ActionRow(select))


class SpecPickerView(discord.ui.LayoutView):
    def __init__(self, owner_id, class_name, signup_raid_id=None,
                 editing_character_id=None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.class_name = class_name
        self.signup_raid_id = signup_raid_id
        self.editing_character_id = editing_character_id
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## {class_name}\n"
                "Choose spec. Ogre pick raid role automatically where possible."
            ), accent_color=ACCENT,
        ))
        select = discord.ui.Select(
            placeholder="Choose your specialisation",
            options=[discord.SelectOption(label=name, value=name)
                     for name in CLASS_SPECS[class_name]],
        )

        async def select_spec(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            spec = select.values[0]
            roles = CLASS_SPECS[self.class_name][spec]
            if len(roles) > 1:
                await interaction.response.edit_message(
                    view=RolePickerView(
                        self.owner_id, self.class_name, spec, roles,
                        signup_raid_id=self.signup_raid_id,
                        editing_character_id=self.editing_character_id,
                    )
                )
            elif self.editing_character_id is not None:
                await apply_profile_update(
                    interaction, self.owner_id, self.editing_character_id,
                    self.class_name, spec, roles[0],
                )
            else:
                await interaction.response.send_modal(
                    CharacterNameModal(self.owner_id, self.class_name, spec, roles[0],
                                       signup_raid_id=self.signup_raid_id)
                )

        select.callback = select_spec
        self.add_item(discord.ui.ActionRow(select))


class RolePickerView(discord.ui.LayoutView):
    def __init__(self, owner_id, class_name, spec, roles, signup_raid_id=None,
                 editing_character_id=None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.class_name = class_name
        self.spec = spec
        self.signup_raid_id = signup_raid_id
        self.editing_character_id = editing_character_id
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## {class_name} · {spec}\n"
                "That spec can cover more than one raid role. Which do you plan to play?"
            ), accent_color=ACCENT,
        ))
        select = discord.ui.Select(
            placeholder="Choose raid role",
            options=[discord.SelectOption(label=role, value=role, emoji=option_emoji(role if role != "DPS" else "Melee")) for role in roles],
        )

        async def select_role(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            if self.editing_character_id is not None:
                await apply_profile_update(
                    interaction, self.owner_id, self.editing_character_id,
                    self.class_name, self.spec, select.values[0],
                )
            else:
                await interaction.response.send_modal(
                    CharacterNameModal(
                        self.owner_id, self.class_name, self.spec, select.values[0],
                        signup_raid_id=self.signup_raid_id,
                    )
                )

        select.callback = select_role
        self.add_item(discord.ui.ActionRow(select))


class ProfileUpdatedView(discord.ui.LayoutView):
    def __init__(self, name, class_name, spec, role):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Ogre updated {discord.utils.escape_markdown(name)}\n"
                f"{class_name} / {spec} · **{role}**"
            ), accent_color=ACCENT,
        ))


async def apply_profile_update(interaction, owner_id, character_id,
                               class_name, spec, role):
    if not is_test_member(interaction) or interaction.user.id != owner_id:
        await interaction.response.send_message(
            "This character isn't yours.", ephemeral=True
        )
        return
    try:
        name = update_character_profile(
            owner_id, character_id, class_name, spec, role
        )
    except (ValueError, sqlite3.Error) as exc:
        await interaction.response.send_message(
            f"Ogre couldn't update character: {exc}", ephemeral=True
        )
        return
    await interaction.response.edit_message(
        view=ProfileUpdatedView(name, class_name, spec, role)
    )


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class RaidPrototype(commands.GroupCog, group_name="raid", group_description="Hyped raid sign-up management"):
    def __init__(self, bot):
        self.bot = bot
        self._sheet_task = None
        self._discord_name_cache = {}
        self._last_sheet_error = None
        self._last_sheet_sync = None
        self._sync_lock = asyncio.Lock()

    def allowed(self, interaction):
        # During testing, live raid panels are ONLY permitted in private #ogre.
        return is_test_owner(interaction)

    def raid_db_path(self):
        return RAID_DB

    async def delete_raid_from_poster(self, interaction, raid_number):
        """Delete the raid card, then raid-scoped rows; never delete characters."""
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can delete raids in private #ogre.",
                ephemeral=True,
            )
            return
        try:
            event = read_raid(RAID_DB, raid_number)
        except sqlite3.Error:
            await interaction.response.send_message(
                "Ogre couldn't read this raid; nothing was deleted.",
                ephemeral=True,
            )
            return
        if event is None:
            await interaction.response.send_message(
                "That raid no longer exists.", ephemeral=True
            )
            return
        channel_id = event.get("panel_channel_id")
        message_id = event.get("panel_message_id")
        # Never delete a message outside the private test channel.
        if message_id and channel_id != TEST_CHANNEL_ID:
            await interaction.response.send_message(
                "Ogre won't delete a raid card outside private #ogre.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        if message_id:
            try:
                message = await interaction.channel.fetch_message(message_id)
                await message.delete()
            except discord.NotFound:
                # Already deleted from Discord; still allow database cleanup.
                pass
            except (discord.Forbidden, discord.HTTPException):
                await interaction.followup.send(
                    "Ogre couldn't remove the Discord raid card. "
                    "Raid data was preserved; please retry.",
                    ephemeral=True,
                )
                return
        try:
            delete_raid(RAID_DB, raid_number)
        except (ValueError, sqlite3.Error):
            await interaction.followup.send(
                "Ogre couldn't delete the saved raid records. "
                "No other raids or characters were touched.",
                ephemeral=True,
            )
            return
        try:
            await interaction.message.edit(
                view=RaidPosterView(self, interaction.user.id)
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        await interaction.followup.send(
            f"Ogre deleted Raid #{raid_number}, including its sign-ups "
            "and attendance. Characters and gear remain saved.",
            ephemeral=True,
        )

    async def cog_load(self):
        # Reattach real panel buttons after restarts so posted panels keep working.
        for event in active_panels(RAID_DB):
            if event.get("panel_channel_id") == TEST_CHANNEL_ID:
                self.bot.add_view(self.make_panel(event), message_id=event["panel_message_id"])
        if raid_sheets.configured():
            self._sheet_task = asyncio.create_task(self.sheet_sync_loop())

    def cog_unload(self):
        if self._sheet_task:
            self._sheet_task.cancel()

    async def sheet_sync_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.sync_raid_sheets()
            except Exception as exc:
                # A Sheets outage may not interrupt Discord raid registrations.
                self._last_sheet_error = str(exc)
                LOGGER.warning("Raid Sheets sync failed: %s", exc)
            await asyncio.sleep(60)

    async def raid_discord_names(self):
        """Resolve guild nicknames on the Discord event loop, not in the Sheets thread."""
        member_ids = await asyncio.to_thread(
            raid_sheets.character_discord_ids, RAID_DB
        )
        guild = self.bot.get_guild(GUILD_ID)
        names = {}
        for member_id in member_ids:
            member = guild.get_member(member_id) if guild is not None else None
            # fetch_member works even with an empty member cache. Only fetch
            # names not resolved in a previous sync, to avoid hourly API churn.
            if member is None and guild is not None and member_id not in self._discord_name_cache:
                try:
                    member = await guild.fetch_member(member_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            if member is not None:
                names[member_id] = member.display_name
            elif member_id in self._discord_name_cache:
                names[member_id] = self._discord_name_cache[member_id]
            else:
                user = self.bot.get_user(member_id)
                if user is not None:
                    names[member_id] = user.name
        self._discord_name_cache.update(names)
        return names

    async def sync_raid_sheets(self):
        async with self._sync_lock:
            names = await self.raid_discord_names()
            counts, changed = await asyncio.to_thread(
                raid_sheets_v3.sync_once, RAID_DB, names
            )
            self._last_sheet_error = None
            self._last_sheet_sync = datetime.now(timezone.utc)
            for raid_id in changed:
                event = read_raid(RAID_DB, raid_id)
                if not event or not event.get("panel_message_id"):
                    continue
                if event.get("panel_channel_id") != TEST_CHANNEL_ID:
                    continue
                channel = self.bot.get_channel(TEST_CHANNEL_ID)
                if channel is None:
                    continue
                try:
                    message = await channel.fetch_message(event["panel_message_id"])
                    await message.edit(view=self.make_panel(event))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    LOGGER.warning("Unable to refresh revealed roster for Raid #%s", raid_id)
            return counts, changed

    async def on_register(self, interaction):
        if not is_test_member(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        await interaction.response.send_message(
            view=ClassPickerView(interaction.user.id), ephemeral=True
        )

    async def on_characters(self, interaction):
        if not is_test_member(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        await interaction.response.send_message(
            character_list_text(interaction.user.id), ephemeral=True
        )

    async def on_signup(self, interaction, raid_id):
        if not is_test_member(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        # Acknowledge immediately to avoid Discord's three-second interaction deadline.
        await interaction.response.defer(ephemeral=True)
        try:
            event = read_raid(RAID_DB, raid_id)
            if not event or not event["signup_open"] or event["roster_published"]:
                await interaction.followup.send(
                    "Sign-ups are closed for this raid.", ephemeral=True
                )
                return
            if not member_characters(interaction.user.id):
                # First-time users register a character and sign up in one flow.
                await interaction.followup.send(
                    view=ClassPickerView(interaction.user.id, signup_raid_id=raid_id),
                    ephemeral=True,
                )
                return
            existing = my_signup(raid_id, interaction.user.id)
            # Components V2 views must not be sent alongside ordinary content.
            await interaction.followup.send(
                view=CharacterActionView(
                    interaction.user.id, raid_id=raid_id, current_signup=existing
                ),
                ephemeral=True,
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.followup.send(
                f"Ogre couldn't open sign-ups: {exc}", ephemeral=True
            )

    async def on_gear(self, interaction):
        if not is_test_member(interaction):
            await interaction.response.send_message(
                "Gear only available in private #ogre.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        if not member_characters(interaction.user.id):
            await interaction.followup.send(
                "Ogre need a character first. Press Sign to register.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            view=CharacterActionView(interaction.user.id, purpose="gear"),
            ephemeral=True,
        )

    async def on_update(self, interaction):
        if not is_test_member(interaction):
            await interaction.response.send_message(
                "Update only available in private #ogre.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        if not member_characters(interaction.user.id):
            await interaction.followup.send(
                "Ogre need a character first. Press Sign to register.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            view=CharacterActionView(interaction.user.id, purpose="update"),
            ephemeral=True,
        )

    async def on_absent(self, interaction, raid_id):
        if not is_test_member(interaction):
            await interaction.response.send_message(
                "Absence only available in private #ogre.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            AbsenceNoteModal(interaction.user.id, raid_id)
        )

    def raid_emoji_map(self):
        """Stable custom markup without guild-cache lookup."""
        return {name.casefold(): guild_emoji(name) for name in EMOJI_IDS}

    def make_panel(self, event):
        async def signup(interaction):
            await self.on_signup(interaction, event["raid_id"])

        async def absent(interaction):
            await self.on_absent(interaction, event["raid_id"])

        return RaidEventPanel(
            event, on_signup=signup, on_update=self.on_update,
            on_gear=self.on_gear, on_absent=absent,
            public_roster=read_public_roster(
                RAID_DB, event["raid_id"], include_details=True
            ),
            emoji_map=self.raid_emoji_map(),
        )

    async def post_panel(self, interaction, event):
        # Update an existing message rather than creating duplicate live panels.
        if (event.get("panel_message_id")
                and event.get("panel_channel_id") == interaction.channel_id):
            try:
                message = await interaction.channel.fetch_message(event["panel_message_id"])
                await message.edit(view=self.make_panel(event))
                await interaction.response.send_message(
                    f"Ogre updated raid panel: {message.jump_url}", ephemeral=True
                )
                if raid_sheets.configured():
                    try:
                        await self.sync_raid_sheets()
                    except Exception as exc:
                        LOGGER.warning("Raid tab sync delayed: %s", exc)
                return
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                await interaction.response.send_message(
                    "Ogre couldn't update the existing raid panel.", ephemeral=True
                )
                return

        # Post as a normal bot message. Avoid an interaction-response header
        # attached to the public-facing panel; only the setup ACK is private.
        await interaction.response.defer(ephemeral=True)
        try:
            message = await interaction.channel.send(
                view=self.make_panel(event),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Ogre couldn't post the raid panel in this channel.", ephemeral=True
            )
            return
        record_panel(RAID_DB, event["raid_id"], interaction.channel_id, message.id)
        await interaction.followup.send(
            f"Ogre posted raid panel: {message.jump_url}", ephemeral=True
        )
        if raid_sheets.configured():
            try:
                await self.sync_raid_sheets()
            except Exception as exc:
                LOGGER.warning("Raid tab sync delayed: %s", exc)

    async def refresh_saved_panel(self, number):
        event = read_raid(RAID_DB, number)
        if not event or event.get("panel_channel_id") != TEST_CHANNEL_ID:
            return
        channel = self.bot.get_channel(TEST_CHANNEL_ID)
        if channel is None:
            return
        message = await channel.fetch_message(event["panel_message_id"])
        await message.edit(view=self.make_panel(event))

    @app_commands.command(
        name="confirm", description="Lock the raid selection and confirm the Discord roster"
    )
    @app_commands.describe(number="Raid number to confirm")
    async def confirm(self, interaction: discord.Interaction, number: int):
        if not self.allowed(interaction):
            await interaction.response.send_message("Owner-only in private #ogre.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.sync_raid_sheets()
            approved = await asyncio.to_thread(raid_sheets_v3.read_selected, RAID_DB, number)
            raid_status(RAID_DB, number, "confirm", confirmed_entries=approved)
            await self.sync_raid_sheets()
            await self.refresh_saved_panel(number)
        except (ValueError, RuntimeError, sqlite3.Error,
                discord.HTTPException) as exc:
            await interaction.followup.send(f"Raid confirm: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Raid #{number} roster confirmed and locked. "
            "Sign-ups are closed. Use /raid reopen to make changes.",
            ephemeral=True,
        )

    @app_commands.command(
        name="reopen", description="Reopen a confirmed raid roster for corrections"
    )
    @app_commands.describe(number="Confirmed raid number")
    async def reopen(self, interaction: discord.Interaction, number: int):
        if not self.allowed(interaction):
            await interaction.response.send_message("Owner-only in private #ogre.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            raid_status(RAID_DB, number, "reopen")
            await self.sync_raid_sheets()
            await self.refresh_saved_panel(number)
        except (ValueError, RuntimeError, sqlite3.Error,
                discord.HTTPException) as exc:
            await interaction.followup.send(f"Raid reopen: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Raid #{number} reopened. Update the raid Sheet, then /raid confirm.",
            ephemeral=True,
        )

    @app_commands.command(
        name="archive", description="Archive completed raid with notes and Warcraft Logs"
    )
    @app_commands.describe(number="Completed raid number",
                           wcl_url="Optional Warcraft Logs report URL",
                           notes="Optional raid review notes")
    async def archive(self, interaction: discord.Interaction, number: int,
                      wcl_url: str | None = None, notes: str | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message("Owner-only in private #ogre.", ephemeral=True)
            return
        notes = (notes or "").strip()
        if len(notes) > 1500:
            await interaction.response.send_message("Keep raid notes under 1500 characters.",
                                                    ephemeral=True)
            return
        url = (wcl_url or "").strip()
        if url:
            parts = urlsplit(url)
            if (len(url) > 500 or parts.scheme != "https"
                    or parts.hostname not in ("www.warcraftlogs.com", "warcraftlogs.com")
                    or parts.username or parts.password
                    or not parts.path.startswith("/reports/")):
                await interaction.response.send_message(
                    "Use a secure https://www.warcraftlogs.com/reports/... link.",
                    ephemeral=True)
                return
        await interaction.response.defer(ephemeral=True)
        try:
            raid_status(RAID_DB, number, "archive", notes=notes, wcl_url=url)
            await self.sync_raid_sheets()
            await self.refresh_saved_panel(number)
        except (ValueError, RuntimeError, sqlite3.Error,
                discord.HTTPException) as exc:
            await interaction.followup.send(f"Raid archive: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Raid #{number} archived with review notes and Warcraft Logs. "
            "Its private tab is hidden, not deleted.",
            ephemeral=True,
        )

    @app_commands.command(name="sync", description="Synchronise raid SQLite data and visible roster with Google Sheets")
    async def sync(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can sync raids in private #ogre.", ephemeral=True
            )
            return
        if not raid_sheets.configured():
            await interaction.response.send_message(
                "Google Sheets not connected to Ogre yet. "
                "Set RAID_GOOGLE_SERVICE_ACCOUNT_B64 and RAID_SHEETS_ID "
                "as Fly secrets, then share the private sheet with "
                "the service account email.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            counts, changed = await self.sync_raid_sheets()
        except Exception as exc:
            self._last_sheet_error = str(exc)
            await interaction.followup.send(
                f"Ogre couldn't sync Google Sheets: {exc}", ephemeral=True
            )
            return
        await interaction.followup.send(
            "Ogre synced raid data to Google Sheets. "
            f"Updated/added: {sum(counts.values())} rows. "
            f"Refreshed {len(changed)} raid roster(s).",
            ephemeral=True,
        )

    @app_commands.command(
        name="poster", description="Open the V2 raid setup menu in private #ogre"
    )
    async def poster(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can open raid poster in private #ogre.",
                ephemeral=True,
            )
            return
        # The poster itself is a normal visible bot message, not an ephemeral preview.
        await interaction.response.defer(ephemeral=True)
        try:
            message = await interaction.channel.send(
                view=RaidPosterView(self, interaction.user.id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, discord.Forbidden, ValueError) as exc:
            await interaction.followup.send(
                f"Ogre couldn't open raid poster: {exc}", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Raid poster ready: {message.jump_url}\n"
            "Choose date, time, instances and roster announcement, then post.",
            ephemeral=True,
        )

    @app_commands.command(name="setup", description="Post or update a real raid panel in private #ogre")
    @app_commands.describe(
        number="Raid number, e.g. 1",
        date_time="Raid start in UK local time: YYYY-MM-DD HH:MM",
        instance_1="First raid instance",
        instance_2="Optional second instance",
        instance_3="Optional third instance",
        roster_announce="Roster announcement in UK time: YYYY-MM-DD HH:MM",
        softres_url="Optional https://softres.it/raid/... link",
    )
    async def raid_setup(self, interaction: discord.Interaction,
                         number: int, date_time: str, instance_1: str,
                         instance_2: str | None = None,
                         instance_3: str | None = None,
                         roster_announce: str | None = None,
                         softres_url: str | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Raid panels can only be set up by Ogre's owner in private #ogre.",
                ephemeral=True,
            )
            return
        try:
            event = save_raid(
                RAID_DB, number, date_time, instance_1, instance_2,
                instance_3, roster_announce, softres_url
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't set up raid: {exc}", ephemeral=True
            )
            return
        await self.post_panel(interaction, event)

    @app_commands.command(name="softres", description="Add, change or remove a raid SoftRes link")
    @app_commands.describe(
        number="Raid number",
        url="Optional public SoftRes link; leave empty to remove it",
    )
    async def softres(self, interaction: discord.Interaction,
                      number: int, url: str | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        try:
            event = set_softres_url(RAID_DB, number, url)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn\u0027t update SoftRes: {exc}", ephemeral=True
            )
            return
        await self.post_panel(interaction, event)

    @app_commands.command(name="panel", description="Refresh the existing real raid panel")
    @app_commands.describe(number="Raid number (leave blank for latest)")
    async def panel(self, interaction: discord.Interaction, number: int | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        event = read_raid(RAID_DB, number) if number is not None else latest_raid(RAID_DB)
        if not event:
            await interaction.response.send_message(
                "No raid configured yet. Use /raid setup first.", ephemeral=True
            )
            return
        await self.post_panel(interaction, event)

    @app_commands.command(
        name="gear-review",
        description="Owner: view a saved character-panel screenshot privately",
    )
    @app_commands.describe(
        character_id="Character ID from RM - Characters or RM - Gear Checks",
    )
    async def gear_review(self, interaction: discord.Interaction, character_id: int):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can inspect screenshots in private #ogre.",
                ephemeral=True,
            )
            return
        try:
            image = last_gear_image(character_id)
        except sqlite3.Error:
            await interaction.response.send_message(
                "Ogre couldn't load that screenshot.", ephemeral=True
            )
            return
        if not image:
            await interaction.response.send_message(
                "Ogre got no screenshot for that character yet.", ephemeral=True
            )
            return
        filename, payload = image
        await interaction.response.send_message(
            file=discord.File(io.BytesIO(payload), filename=filename),
            ephemeral=True,
        )

    @app_commands.command(
        name="gear", description="Upload SimC text or a character-panel screenshot"
    )
    @app_commands.describe(
        character="Your saved character name",
        file="SimC (.txt/.simc) or character-panel PNG/JPG/WEBP",
    )
    async def gear_file(self, interaction: discord.Interaction,
                        character: str, file: discord.Attachment):
        if not is_test_member(interaction):
            await interaction.response.send_message(
                "Gear uploads are only available in private #ogre.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        matches = [
            r for r in member_characters(interaction.user.id)
            if r[1].casefold() == character.strip().casefold()
        ]
        if not matches:
            await interaction.followup.send(
                "Ogre not know that character. Press Sign to register first.",
                ephemeral=True,
            )
            return
        filename = file.filename.lower()
        simc_file = filename.endswith((".txt", ".simc"))
        image_file = filename.endswith((".png", ".jpg", ".jpeg", ".webp"))
        if not (simc_file or image_file):
            await interaction.followup.send(
                "Use a SimC .txt/.simc or PNG/JPG/WEBP screenshot.",
                ephemeral=True,
            )
            return
        limit = 200_000 if simc_file else 8 * 1024 * 1024
        if file.size > limit:
            await interaction.followup.send(
                "Ogre say that file too big.", ephemeral=True
            )
            return
        try:
            data = await file.read()
            if len(data) > limit:
                raise ValueError("Uploaded file exceeds the size limit.")
            if simc_file:
                save_gear(interaction.user.id, matches[0][0],
                          data.decode("utf-8-sig"))
            else:
                save_gear_image(interaction.user.id, matches[0][0],
                                file.filename, data)
        except (ValueError, UnicodeDecodeError, sqlite3.Error,
                discord.HTTPException) as exc:
            await interaction.followup.send(
                f"Ogre couldn't update gear: {exc}", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Ogre saved {'SimC' if simc_file else 'screenshot'} for "
            f"**{discord.utils.escape_markdown(matches[0][1])}**.",
            ephemeral=True,
        )

    @gear_file.autocomplete("character")
    async def autocomplete_character(self, interaction: discord.Interaction, current: str):
        if not is_test_member(interaction):
            return []
        return [
            app_commands.Choice(name=row[1], value=row[1])
            for row in member_characters(interaction.user.id)
            if current.casefold() in row[1].casefold()
        ][:25]

    @app_commands.command(name="register", description="Register or update a character")
    async def register(self, interaction: discord.Interaction):
        await self.on_register(interaction)

    @app_commands.command(name="characters", description="Show your saved characters (private)")
    async def characters(self, interaction: discord.Interaction):
        await self.on_characters(interaction)


async def setup(bot):
    create_schema()
    ensure_raid_fields(RAID_DB)
    await bot.add_cog(RaidPrototype(bot))
