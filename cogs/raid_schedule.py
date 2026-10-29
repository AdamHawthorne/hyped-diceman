"""Raid schedule storage and public-facing Components V2 raid panel.

An event is configured in the private testing channel for now. Sheets-to-bot
synchronisation is separate: the Google Sheet does not authorise a Fly bot.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import discord

UK = ZoneInfo("Europe/London")
RAID_IMAGE = "https://i.vgy.me/XIszIH.png"
ACCENT = 0xA87535
RAID_COLUMNS = {
    "instance_1": "TEXT",
    "instance_2": "TEXT",
    "instance_3": "TEXT",
    "roster_announce_at": "TEXT",
    "panel_message_id": "INTEGER",
    "panel_channel_id": "INTEGER",
    "softres_url": "TEXT",
}


@contextmanager
def database(path):
    """Commit or roll back SQL and always close the connection (Windows-safe)."""
    con = sqlite3.connect(path, timeout=20)
    try:
        with con:
            yield con
    finally:
        con.close()


def parse_uk_datetime(value):
    """Interpret an explicit YYYY-MM-DD HH:MM local UK clock time."""
    try:
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError("Use YYYY-MM-DD HH:MM (UK time), e.g. 2026-11-05 20:00.") from exc
    aware = naive.replace(tzinfo=UK)
    if aware.astimezone(timezone.utc).astimezone(UK).replace(tzinfo=None) != naive:
        raise ValueError("That UK time does not exist because of a clock change.")
    return aware


def format_discord_date(value, *, style="F", with_relative=True):
    """Discord full date or relative countdown, not both unless requested."""
    if not value:
        return "To be confirmed"
    if style not in ("F", "R"):
        raise ValueError("Unsupported Discord date style.")
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("Raid times must have a timezone.")
    timestamp = int(instant.timestamp())
    display = f"<t:{timestamp}:{style}>"
    if with_relative and style != "R":
        return display + f" · <t:{timestamp}:R>"
    return display


def ensure_raid_fields(path):
    """Add new columns without clearing any existing sign-ups or characters."""
    with database(path) as con:
        names = {row[1] for row in con.execute("PRAGMA table_info(raids)")}
        if not names:
            raise RuntimeError("Raid schema must be created before schedule migration.")
        for field, sql_type in RAID_COLUMNS.items():
            if field not in names:
                con.execute(f"ALTER TABLE raids ADD COLUMN {field} {sql_type}")


def validate_softres_url(value):
    """Only display a public HTTPS SoftRes raid URL when one is provided."""
    if not value or not value.strip():
        return None
    url = value.strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port
    except ValueError as exc:
        raise ValueError("Enter a valid SoftRes HTTPS link.") from exc
    if (len(url) > 1500 or parts.scheme != "https"
            or not (host == "softres.it" or host.endswith(".softres.it"))
            or parts.username is not None or parts.password is not None
            or not parts.path.startswith("/raid/")):
        raise ValueError("Paste a https://softres.it/raid/... link.")
    return url


def set_softres_url(path, number, url):
    """Change just the SoftRes link; leave the raid event/roster untouched."""
    value = validate_softres_url(url)
    with database(path) as con:
        cursor = con.execute(
            "UPDATE raids SET softres_url=? WHERE raid_id=?", (value, number)
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Raid #{number} not found. Use /raid setup first.")
    return read_raid(path, number)


def save_raid(path, number, raid_date, instance_1, instance_2=None,
              instance_3=None, roster_announce=None, softres_url=None):
    softres = validate_softres_url(softres_url)
    if not 1 <= number <= 100000:
        raise ValueError("Raid number must be between 1 and 100000.")
    start = parse_uk_datetime(raid_date)
    announce = parse_uk_datetime(roster_announce) if roster_announce else None
    instances = [(part or "").strip() for part in (instance_1, instance_2, instance_3)]
    if not instances[0] or any(len(part) > 80 for part in instances):
        raise ValueError("Enter an instance #1 (80 characters max per instance).")
    if instances[2] and not instances[1]:
        raise ValueError("Add instance #2 before instance #3.")
    if announce and announce > start:
        raise ValueError("Roster announcement must not be after the raid starts.")
    with database(path) as con:
        con.execute("""
            INSERT INTO raids(raid_id, title, raid_at, instance_1, instance_2,
                              instance_3, roster_announce_at, softres_url, signup_open)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(raid_id) DO UPDATE SET
                title=excluded.title, raid_at=excluded.raid_at,
                instance_1=excluded.instance_1, instance_2=excluded.instance_2,
                instance_3=excluded.instance_3,
                roster_announce_at=excluded.roster_announce_at,
                softres_url=COALESCE(excluded.softres_url, raids.softres_url),
                signup_open=1
        """, (number, f"<Hyped> Raid #{number}", start.isoformat(),
              instances[0], instances[1] or None, instances[2] or None,
              announce.isoformat() if announce else None, softres))
    return read_raid(path, number)


def read_raid(path, number):
    with database(path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM raids WHERE raid_id=?", (number,)).fetchone()
        return dict(row) if row else None


def latest_raid(path):
    with database(path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM raids ORDER BY raid_id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def list_raids(path):
    with database(path) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(
            "SELECT * FROM raids ORDER BY raid_id DESC LIMIT 24"
        )]


def active_panels(path):
    with database(path) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(
            "SELECT * FROM raids WHERE panel_message_id IS NOT NULL"
        )]


def delete_raid(path, number):
    """Delete one raid and its applications/attendance, not character or SimC data.

    Explicit dependent-row deletion makes this safe even on SQLite connections
    where foreign key enforcement is enabled, while retaining all other raids.
    """
    with database(path) as con:
        con.execute("PRAGMA foreign_keys=ON")
        event = con.execute(
            "SELECT raid_id FROM raids WHERE raid_id=?", (number,)
        ).fetchone()
        if not event:
            raise ValueError(f"Raid #{number} no longer exists.")
        con.execute("DELETE FROM signups WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raid_absences WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raid_public_roster WHERE raid_id=?", (number,))
        con.execute("DELETE FROM attendance WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raids WHERE raid_id=?", (number,))
    return True

def read_public_roster(path, raid_id):
    """Only manually revealed rows from the private sheet's approved roster."""
    with database(path) as con:
        return con.execute(
            "SELECT name, assignment FROM raid_public_roster "
            "WHERE raid_id=? ORDER BY position", (raid_id,)
        ).fetchall()


def record_panel(path, number, channel_id, message_id):
    with database(path) as con:
        con.execute(
            "UPDATE raids SET panel_channel_id=?, panel_message_id=? WHERE raid_id=?",
            (channel_id, message_id, number),
        )


def raid_panel_text(event, public_roster=()):
    """One roster section; only the explicitly revealed selected names appear."""
    title = event["title"]  # Generated from a validated integer, not player input.
    instances = [
        discord.utils.escape_mentions(discord.utils.escape_markdown(event[key]))
        for key in ("instance_1", "instance_2", "instance_3")
        if event.get(key)
    ]
    roster_date = format_discord_date(
        event.get("roster_announce_at"), style="R", with_relative=False
    )
    people = []
    for name, assignment in public_roster:
        clean = discord.utils.escape_mentions(discord.utils.escape_markdown(name))
        info = discord.utils.escape_mentions(
            discord.utils.escape_markdown(assignment or "")
        )
        item = f"• **{clean}**" + (f" — {info}" if info else "")
        if sum(map(len, people)) + len(item) > 3100:
            break
        people.append(item)
    roster_lines = ("\n".join(people) + "\n\n") if people else ""
    softres = event.get("softres_url")
    softres_line = f"\n[SoftRes sign-up]({softres})" if softres else ""
    return (
        f"## {title}\n"
        f"**Date:** {format_discord_date(event['raid_at'], with_relative=False)}\n\n"
        + "**Raid:** " + ", ".join(instances)
        + "\n**Check:** <#1563663258047619123>"
        + softres_line
        + "\n\n### Roster\n"
        + roster_lines
        + f"**Roster Announcement:** {roster_date}"
    )


class RaidEventPanel(discord.ui.LayoutView):
    """Real Discord V2 message, persistent across bot restarts."""
    def __init__(self, event, *, on_register=None, on_characters=None, on_signup=None,
                 on_gear=None, on_update=None, on_absent=None, public_roster=()):
        super().__init__(timeout=None)
        event_id = event["raid_id"]
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(raid_panel_text(event, public_roster)),
                accessory=discord.ui.Thumbnail(
                    RAID_IMAGE, description="Hyped raid image"
                ),
            ),
            accent_color=ACCENT,
        ))
        controls = discord.ui.ActionRow()
        sign_up = discord.ui.Button(
            label="Sign up", custom_id=f"hyped_raid_{event_id}_signup",
            style=discord.ButtonStyle.success,
            disabled=not bool(event.get("signup_open")) or bool(event.get("roster_published")),
        )
        update = discord.ui.Button(
            label="Update", custom_id=f"hyped_raid_{event_id}_update",
            style=discord.ButtonStyle.secondary, disabled=on_update is None,
        )
        gear = discord.ui.Button(
            label="Gear", custom_id=f"hyped_raid_{event_id}_gear",
            style=discord.ButtonStyle.secondary, disabled=on_gear is None,
        )
        absent = discord.ui.Button(
            label="Absent", custom_id=f"hyped_raid_{event_id}_absent",
            style=discord.ButtonStyle.danger, disabled=on_absent is None,
        )
        if on_signup is not None:
            sign_up.callback = on_signup
        else:
            sign_up.disabled = True
        if on_update is not None:
            update.callback = on_update
        if on_gear is not None:
            gear.callback = on_gear
        if on_absent is not None:
            absent.callback = on_absent
        for control in (sign_up, update, gear, absent):
            controls.add_item(control)
        self.add_item(controls)
