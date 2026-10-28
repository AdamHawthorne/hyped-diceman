"""Private raid signup prototype. No public raid posts or Sheets credentials required."""
import os
import sqlite3
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


class RaidSignupPreview(discord.ui.LayoutView):
    """Visual-only proposal; buttons are deliberately disabled."""
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## Raid sign-up:\n"
                "Ogre building raid. You coming?\n\n"
                "**Sign-ups not open yet**\n"
                "-# Ogre collecting names. Roster stays hidden until leadership picks."
            ),
            accent_color=ACCENT,
        ))
        self.add_item(discord.ui.ActionRow(
            discord.ui.Button(label="Sign up", style=discord.ButtonStyle.primary, disabled=True),
            discord.ui.Button(label="My characters", style=discord.ButtonStyle.secondary, disabled=True),
            discord.ui.Button(label="Update gear", style=discord.ButtonStyle.secondary, disabled=True),
        ))


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class RaidPrototype(commands.GroupCog, group_name="raid", group_description="Raid management prototype"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="preview", description="Private raid sign-up panel preview")
    async def preview(self, interaction: discord.Interaction):
        if (interaction.user.id != OWNER_ID or interaction.guild_id != GUILD_ID
                or interaction.channel_id != TEST_CHANNEL_ID):
            await interaction.response.send_message(
                "Ogre only show raid prototype to owner in private #ogre.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(view=RaidSignupPreview(), ephemeral=True)


async def setup(bot):
    create_schema()
    await bot.add_cog(RaidPrototype(bot))
