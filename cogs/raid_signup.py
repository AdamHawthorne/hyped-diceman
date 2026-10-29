"""Private raid signup prototype. No public raid posts or Sheets credentials required."""
import os
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import contextmanager

import discord
from discord import app_commands
from discord.ext import commands

from cogs.raid_poster import RaidPosterView
from cogs.raid_schedule import (
    RaidEventPanel, active_panels, ensure_raid_fields, latest_raid, list_raids,
    read_raid, record_panel, save_raid, set_softres_url, delete_raid,
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


def save_signup(raid_id, discord_id, character_id, path=RAID_DB):
    """Only accept an applicant's own character with a submitted SimC export."""
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
        gear = con.execute(
            "SELECT 1 FROM gear_exports WHERE character_id=? LIMIT 1",
            (character_id,),
        ).fetchone()
        if not gear:
            raise ValueError(
                "Ogre need your SimC export first. Use Update gear, then sign up."
            )
        con.execute("""
            INSERT INTO signups(raid_id, discord_id, character_id, availability, submitted_at)
            VALUES (?, ?, ?, 'Available', ?)
            ON CONFLICT(raid_id, discord_id) DO UPDATE SET
                character_id=excluded.character_id, availability='Available',
                submitted_at=excluded.submitted_at
        """, (raid_id, discord_id, character_id, datetime.now(timezone.utc).isoformat()))


def my_signup(raid_id, discord_id, path=RAID_DB):
    with database(path) as con:
        return con.execute(
            "SELECT c.name, s.availability FROM signups s "
            "JOIN characters c ON c.character_id=s.character_id "
            "WHERE s.raid_id=? AND s.discord_id=?",
            (raid_id, discord_id),
        ).fetchone()


def withdraw_signup(raid_id, discord_id, path=RAID_DB):
    with database(path) as con:
        event = con.execute(
            "SELECT signup_open, roster_published FROM raids WHERE raid_id=?",
            (raid_id,),
        ).fetchone()
        if not event or not event[0] or event[1]:
            raise ValueError("Sign-ups aren't open for this raid.")
        return con.execute(
            "DELETE FROM signups WHERE raid_id=? AND discord_id=?",
            (raid_id, discord_id),
        ).rowcount


class GearPasteModal(discord.ui.Modal):
    def __init__(self, owner_id, char_id, char_name):
        super().__init__(title="Update SimC gear snapshot")
        self.owner_id = owner_id
        self.char_id = char_id
        self.char_name = char_name
        self.export = discord.ui.TextInput(
            label="Paste SimC export (up to 4000 characters)",
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.export)

    async def on_submit(self, interaction):
        if not is_test_owner(interaction) or interaction.user.id != self.owner_id:
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        try:
            save_gear(self.owner_id, self.char_id, self.export.value)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't update gear: {exc}", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Gear saved for **{discord.utils.escape_markdown(self.char_name)}**. "
            "Raid sign-up not changed.", ephemeral=True
        )


class CharacterActionView(discord.ui.LayoutView):
    """Ephemeral member-only choices; never publish applicants or gear contents."""
    def __init__(self, owner_id, *, raid_id=None, purpose="signup"):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.raid_id = raid_id
        self.purpose = purpose
        rows = member_characters(owner_id)
        if purpose == "signup":
            hint = ("Choose the character for this raid. A SimC snapshot is required. "
                    "Only leadership can see the applicant list.")
        else:
            hint = ("Choose which character to update. Paste short SimC exports here; "
                    "use /raid gear for larger .txt/.simc files.")
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay("## " + ("Raid sign-up" if purpose == "signup" else "Update gear")
                                   + "\n" + hint), accent_color=ACCENT,
        ))
        if not rows:
            return
        selection = discord.ui.Select(
            placeholder="Choose your saved character",
            options=[
                discord.SelectOption(
                    label=name[:100], value=str(char_id),
                    description=f"{class_name} / {spec} · {role}"[:100],
                ) for char_id, name, class_name, spec, role, _ in rows[:25]
            ],
        )

        async def select_character(interaction):
            if not is_test_owner(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("Private owner test only.", ephemeral=True)
                return
            char_id = int(selection.values[0])
            match = next((r for r in rows if r[0] == char_id), None)
            if not match:
                await interaction.response.send_message(
                    "Character list expired. Reopen from the raid panel.", ephemeral=True
                )
                return
            if self.purpose == "gear":
                await interaction.response.send_modal(
                    GearPasteModal(self.owner_id, char_id, match[1])
                )
                return
            try:
                save_signup(self.raid_id, self.owner_id, char_id)
            except (ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            await interaction.response.send_message(
                f"Ogre got your sign-up for Raid #{self.raid_id}: "
                f"**{discord.utils.escape_markdown(match[1])}**. "
                "Applicant list remains hidden.", ephemeral=True
            )

        selection.callback = select_character
        self.add_item(discord.ui.ActionRow(selection))
        if purpose == "signup":
            withdraw = discord.ui.Button(
                label="Withdraw my sign-up", style=discord.ButtonStyle.secondary,
            )

            async def on_withdraw(interaction):
                if not is_test_owner(interaction) or interaction.user.id != self.owner_id:
                    await interaction.response.send_message("Private owner test only.", ephemeral=True)
                    return
                try:
                    removed = withdraw_signup(self.raid_id, self.owner_id)
                except (ValueError, sqlite3.Error) as exc:
                    await interaction.response.send_message(str(exc), ephemeral=True)
                    return
                await interaction.response.send_message(
                    "Ogre withdrew your sign-up." if removed else "No sign-up to withdraw.",
                    ephemeral=True,
                )

            withdraw.callback = on_withdraw
            self.add_item(discord.ui.ActionRow(withdraw))


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

    async def on_signup(self, interaction, raid_id):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        if not member_characters(interaction.user.id):
            await interaction.response.send_message(
                "Register your character first with /raid register.", ephemeral=True
            )
            return
        existing = my_signup(raid_id, interaction.user.id)
        await interaction.response.send_message(
            (f"Current sign-up: **{discord.utils.escape_markdown(existing[0])}**.\n"
             if existing else "")
            + "Choose a character below to sign up or change your character.",
            view=CharacterActionView(interaction.user.id, raid_id=raid_id),
            ephemeral=True,
        )

    async def on_gear(self, interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message("Private owner test only.", ephemeral=True)
            return
        if not member_characters(interaction.user.id):
            await interaction.response.send_message(
                "Register your character first with /raid register.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            view=CharacterActionView(interaction.user.id, purpose="gear"), ephemeral=True
        )

    def make_panel(self, event):
        async def signup(interaction):
            await self.on_signup(interaction, event["raid_id"])
        return RaidEventPanel(
            event, on_register=self.on_register, on_characters=self.on_characters,
            on_signup=signup, on_gear=self.on_gear,
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
        name="gear", description="Upload a long .txt/.simc character gear export"
    )
    @app_commands.describe(
        character="Your saved character name",
        file="SimulationCraft text export file (.txt or .simc)",
    )
    async def gear_file(self, interaction: discord.Interaction,
                        character: str, file: discord.Attachment):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Private owner test only.", ephemeral=True
            )
            return
        matches = [r for r in member_characters(interaction.user.id)
                   if r[1].casefold() == character.strip().casefold()]
        if not matches:
            await interaction.response.send_message(
                "Ogre not know that character. Use /raid characters first.",
                ephemeral=True,
            )
            return
        if not file.filename.lower().endswith((".txt", ".simc")) or file.size > 200000:
            await interaction.response.send_message(
                "Upload a .txt or .simc file, 200 KB maximum.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await file.read()
            if len(data) > 200000:
                raise ValueError("SimC upload exceeds 200 KB.")
            content = data.decode("utf-8-sig")
            save_gear(interaction.user.id, matches[0][0], content)
        except (ValueError, UnicodeDecodeError, sqlite3.Error, discord.HTTPException) as exc:
            await interaction.followup.send(
                f"Ogre couldn't update gear: {exc}", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Ogre saved new SimC snapshot for "
            f"**{discord.utils.escape_markdown(matches[0][1])}**. "
            "Raid sign-up stays unchanged.",
            ephemeral=True,
        )

    @gear_file.autocomplete("character")
    async def autocomplete_character(self, interaction: discord.Interaction, current: str):
        if not is_test_owner(interaction):
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
