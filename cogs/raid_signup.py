"""Private raid signup prototype. No public raid posts or Sheets credentials required."""
import os
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

import discord
from discord import app_commands
from discord.ext import commands

GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
TEST_CHANNEL_ID = 1564594901746716713
RAID_CHANNEL_ID = 1563663151323553802
RAID_DB = os.getenv("RAID_DB_PATH", "/data/raid_signup.db" if os.path.isdir("/data") else "raid_signup.db")
ACCENT = 0xA87535

SHEET_TABS = {
    "Characters": ("Character ID", "Discord ID", "Discord Name", "Character", "Realm", "Class", "Spec", "Role", "Main/Alt", "Last Gear Update"),
    "Sign-ups": ("Raid ID", "Raid Date", "Discord ID", "Character ID", "Availability", "Notes", "SimC Updated", "Submitted At"),
    "Gear Checks": ("Character ID", "Character", "Discord ID", "Exported At", "SimC Format", "Equipment Data", "Review Status", "Review Notes"),
    "Roster Builder": ("Raid ID", "Raid Date", "Discord ID", "Character ID", "Selection", "Assigned Role", "Bench", "Published At"),
    "Attendance": ("Raid ID", "Raid Date", "Discord ID", "Discord Name", "Character ID", "Character", "Roster Status", "Attendance", "Late Minutes", "AFK Minutes", "Excused", "Leadership Notes", "Updated At"),
    "Raid History": ("Raid ID", "Raid Date", "Instance", "Roster Published", "Completed", "Notes"),
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
        CREATE TABLE IF NOT EXISTS gear_exports(
            export_id INTEGER PRIMARY KEY,
            character_id INTEGER NOT NULL REFERENCES characters(character_id),
            submitted_at TEXT NOT NULL,
            format TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            parsed_json TEXT
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


def save_character(discord_id, name, realm, class_name, spec, role, path=RAID_DB):
    """Insert/update one character belonging to the submitting Discord user."""
    name, realm, class_name, spec = (x.strip() for x in (name, realm, class_name, spec))
    if not all((name, class_name, spec)) or len(name) > 40 or len(realm) > 50:
        raise ValueError("Enter a valid character name, class and spec.")
    if role not in ("Tank", "Healer", "DPS"):
        raise ValueError("Choose Tank, Healer or DPS.")
    with database(path) as con:
        con.execute("""
            INSERT INTO characters (discord_id, name, realm, class_name, spec, role)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_id, name, realm) DO UPDATE SET
                class_name=excluded.class_name, spec=excluded.spec, role=excluded.role
        """, (discord_id, name, realm, class_name, spec, role))


def list_characters(discord_id, path=RAID_DB):
    with database(path) as con:
        return con.execute("""
            SELECT name, realm, class_name, spec, role
            FROM characters WHERE discord_id = ? ORDER BY is_main DESC, name
        """, (discord_id,)).fetchall()


class CharacterModal(discord.ui.Modal, title="Ogre remember your character"):
    name = discord.ui.TextInput(label="Character name", max_length=40)
    realm = discord.ui.TextInput(label="Realm (optional)", required=False, max_length=50)
    class_name = discord.ui.TextInput(label="Class", max_length=40)
    spec = discord.ui.TextInput(label="Specialisation", max_length=50)
    role = discord.ui.TextInput(label="Role: Tank / Healer / DPS", max_length=6)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            save_character(interaction.user.id, self.name.value, self.realm.value,
                           self.class_name.value, self.spec.value,
                           self.role.value.strip().title().replace("Dps", "DPS"))
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"Ogre couldn't save: {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Ogre remember **{discord.utils.escape_markdown(self.name.value)}**. "
            "Use My characters to see saved profile.", ephemeral=True)


class RaidSignupPreview(discord.ui.LayoutView):
    """Private, owner-only development panel. Sign-ups and gear upload stay disabled."""
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## Raid sign-up:\n"
                "Ogre building raid. You coming?\n\n"
                "**Sign-ups not open yet**\n"
                "-# Ogre collecting names. Roster stays hidden until leadership picks."
            ), accent_color=ACCENT,
        ))
        row = discord.ui.ActionRow()
        signup = discord.ui.Button(label="Sign up", style=discord.ButtonStyle.primary, disabled=True)
        chars = discord.ui.Button(label="My characters", style=discord.ButtonStyle.secondary)
        gear = discord.ui.Button(label="Update gear", style=discord.ButtonStyle.secondary, disabled=True)
        async def show_characters(interaction):
            if interaction.user.id != OWNER_ID or interaction.channel_id != TEST_CHANNEL_ID:
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            rows = list_characters(interaction.user.id)
            lines = [f"**{discord.utils.escape_markdown(n)}** ({r or 'realm unknown'}) · {c} / {s} · {role}"
                     for n, r, c, s, role in rows]
            await interaction.response.send_message(
                "## Ogre knows your characters\n" + ("\n".join(lines) if lines else "No characters registered yet."),
                ephemeral=True)
        chars.callback = show_characters
        row.add_item(signup)
        row.add_item(chars)
        row.add_item(gear)
        self.add_item(row)


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class RaidPrototype(commands.GroupCog, group_name="raid", group_description="Raid management prototype"):
    def __init__(self, bot):
        self.bot = bot

    def allowed(self, interaction):
        return (interaction.user.id == OWNER_ID and interaction.guild_id == GUILD_ID
                and interaction.channel_id == TEST_CHANNEL_ID)

    @app_commands.command(name="preview", description="Private raid sign-up panel preview")
    async def preview(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Ogre only show raid prototype to owner in private #ogre.", ephemeral=True)
            return
        await interaction.response.send_message(view=RaidSignupPreview(), ephemeral=True)

    @app_commands.command(name="register", description="Register or update a character (private prototype)")
    async def register(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        await interaction.response.send_modal(CharacterModal())


async def setup(bot):
    create_schema()
    await bot.add_cog(RaidPrototype(bot))
