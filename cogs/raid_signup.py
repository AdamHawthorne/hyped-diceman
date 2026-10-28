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


def save_character(discord_id, name, class_name, spec, role, path=RAID_DB):
    """Update by player + character name, including records from the old realm form.

    Keep the legacy realm column in SQLite for migration compatibility, but never
    request or display it. Never modify a profile belonging to another Discord ID.
    """
    name = name.strip()
    if not name or len(name) > 40:
        raise ValueError("Enter a character name (40 characters maximum).")
    if role not in CLASS_SPECS.get(class_name, {}).get(spec, ()):
        raise ValueError("Choose a valid class, spec and raid role.")
    with database(path) as con:
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
        else:
            con.execute(
                "INSERT INTO characters "
                "(discord_id, name, realm, class_name, spec, role) "
                "VALUES (?, ?, '', ?, ?, ?)",
                (discord_id, name, class_name, spec, role),
            )


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


class CharacterNameModal(discord.ui.Modal):
    def __init__(self, owner_id, class_name, spec, role):
        super().__init__(title="Save character")
        self.owner_id = owner_id
        self.class_name = class_name
        self.spec = spec
        self.role = role
        self.name = discord.ui.TextInput(
            label="Character name", placeholder="e.g. Snore Mon", max_length=40
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_test_owner(interaction) or interaction.user.id != self.owner_id:
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        try:
            save_character(interaction.user.id, self.name.value,
                           self.class_name, self.spec, self.role)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't save character: {exc}", ephemeral=True
            )
            return
        name = discord.utils.escape_markdown(self.name.value.strip())
        await interaction.response.send_message(
            f"Ogre remember **{name}** · {self.class_name} / {self.spec} · "
            f"**{self.role}**.\nUse **/raid characters** to see saved profiles.",
            ephemeral=True,
        )


class ClassPickerView(discord.ui.LayoutView):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## Register character\n"
                "Ogre need your class first.\n"
                "-# Hyped · PvP ruleset · no realm needed."
            ), accent_color=ACCENT,
        ))
        select = discord.ui.Select(
            placeholder="Choose your class",
            options=[discord.SelectOption(label=name, value=name)
                     for name in CLASS_SPECS],
        )

        async def select_class(interaction):
            if not is_test_owner(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=SpecPickerView(self.owner_id, select.values[0])
            )

        select.callback = select_class
        self.add_item(discord.ui.ActionRow(select))


class SpecPickerView(discord.ui.LayoutView):
    def __init__(self, owner_id, class_name):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.class_name = class_name
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
            if not is_test_owner(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            spec = select.values[0]
            roles = CLASS_SPECS[self.class_name][spec]
            if len(roles) > 1:
                await interaction.response.edit_message(
                    view=RolePickerView(self.owner_id, self.class_name, spec, roles)
                )
            else:
                await interaction.response.send_modal(
                    CharacterNameModal(self.owner_id, self.class_name, spec, roles[0])
                )

        select.callback = select_spec
        self.add_item(discord.ui.ActionRow(select))


class RolePickerView(discord.ui.LayoutView):
    def __init__(self, owner_id, class_name, spec, roles):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.class_name = class_name
        self.spec = spec
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## {class_name} · {spec}\n"
                "That spec can cover more than one raid role. Which do you plan to play?"
            ), accent_color=ACCENT,
        ))
        select = discord.ui.Select(
            placeholder="Choose raid role",
            options=[discord.SelectOption(label=role, value=role) for role in roles],
        )

        async def select_role(interaction):
            if not is_test_owner(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            await interaction.response.send_modal(
                CharacterNameModal(self.owner_id, self.class_name, self.spec, select.values[0])
            )

        select.callback = select_role
        self.add_item(discord.ui.ActionRow(select))


class RaidSignupPreview(discord.ui.LayoutView):
    """Private owner-only test. Raid sign-ups and gear submission stay disabled."""
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
        signup = discord.ui.Button(
            label="Sign up", style=discord.ButtonStyle.primary, disabled=True
        )
        register = discord.ui.Button(
            label="Register character", style=discord.ButtonStyle.secondary
        )
        chars = discord.ui.Button(
            label="My characters", style=discord.ButtonStyle.secondary
        )
        gear = discord.ui.Button(
            label="Update gear", style=discord.ButtonStyle.secondary, disabled=True
        )

        async def start_register(interaction):
            if not is_test_owner(interaction):
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            await interaction.response.send_message(
                view=ClassPickerView(interaction.user.id), ephemeral=True
            )

        async def show_characters(interaction):
            if not is_test_owner(interaction):
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            await interaction.response.send_message(
                character_list_text(interaction.user.id), ephemeral=True
            )

        register.callback = start_register
        chars.callback = show_characters
        for control in (signup, register, chars, gear):
            row.add_item(control)
        self.add_item(row)


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class RaidPrototype(commands.GroupCog, group_name="raid", group_description="Raid management prototype"):
    def __init__(self, bot):
        self.bot = bot

    def allowed(self, interaction):
        return is_test_owner(interaction)

    @app_commands.command(name="preview", description="Private raid sign-up panel preview")
    async def preview(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Ogre only show raid prototype to owner in private #ogre.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            view=RaidSignupPreview(), ephemeral=True
        )

    @app_commands.command(name="register", description="Register or update a character (private prototype)")
    async def register(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        await interaction.response.send_message(
            view=ClassPickerView(interaction.user.id), ephemeral=True
        )

    @app_commands.command(name="characters", description="Show your saved characters (private)")
    async def characters(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        await interaction.response.send_message(
            character_list_text(interaction.user.id), ephemeral=True
        )


async def setup(bot):
    create_schema()
    await bot.add_cog(RaidPrototype(bot))
