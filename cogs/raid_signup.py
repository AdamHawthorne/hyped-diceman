"""Private raid signup prototype. No public raid posts or Sheets credentials required."""
import os
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

import discord
from discord import app_commands
from discord.ext import commands

from cogs.raid_schedule import (
    RaidEventPanel, active_panels, ensure_raid_fields, latest_raid,
    read_raid, record_panel, save_raid, set_softres_url,
)

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


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class RaidPrototype(commands.GroupCog, group_name="raid", group_description="Hyped raid sign-up management"):
    def __init__(self, bot):
        self.bot = bot

    def allowed(self, interaction):
        # During testing, live raid panels are ONLY permitted in private #ogre.
        return is_test_owner(interaction)

    async def cog_load(self):
        # Reattach real panel buttons after restarts so posted panels keep working.
        for event in active_panels(RAID_DB):
            if event.get("panel_channel_id") == TEST_CHANNEL_ID:
                self.bot.add_view(self.make_panel(event), message_id=event["panel_message_id"])

    async def on_register(self, interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        await interaction.response.send_message(
            view=ClassPickerView(interaction.user.id), ephemeral=True
        )

    async def on_characters(self, interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        await interaction.response.send_message(
            character_list_text(interaction.user.id), ephemeral=True
        )

    def make_panel(self, event):
        return RaidEventPanel(
            event, on_register=self.on_register, on_characters=self.on_characters
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
