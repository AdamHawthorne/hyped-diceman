"""Raid schedule storage and public-facing Components V2 raid panel.

An event is configured in the private testing channel for now. Sheets-to-bot
synchronisation is separate: the Google Sheet does not authorise a Fly bot.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import discord
from cogs.forever_role import ACCENT as LEADERSHIP_ACCENT

UK = ZoneInfo("Europe/London")
RAID_IMAGE = "https://i.vgy.me/XIszIH.png"  # Poster thumbnail stays unchanged.
RAID_PANEL_IMAGE = "https://i.vgy.me/asI88C.png"
RAID_PANEL_BANNER = "https://i.vgy.me/y6bCxy.png"
ACCENT = 0xA87535  # Keep the private raid poster setup colour.
RAID_PANEL_ACCENT = LEADERSHIP_ACCENT
RAID_COLUMNS = {
    "instance_1": "TEXT",
    "instance_2": "TEXT",
    "instance_3": "TEXT",
    "roster_announce_at": "TEXT",
    "panel_message_id": "INTEGER",
    "panel_channel_id": "INTEGER",
    "softres_url": "TEXT",
    "custom_title": "TEXT",
    "banner_url": "TEXT",
    "roster_posted_at": "TEXT",
    "roster_updated_at": "TEXT",
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
    if style not in ("F", "R", "D", "t"):
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


def validate_banner_url(value):
    """Allow an optional public HTTPS direct image link; blank uses default art."""
    if not value or not value.strip():
        return None
    url = value.strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port
    except ValueError as exc:
        raise ValueError("Enter a valid HTTPS image URL.") from exc
    if (len(url) > 512 or parts.scheme != "https" or not host
            or host in {"localhost", "127.0.0.1", "::1"}
            or parts.username is not None or parts.password is not None
            or not parts.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))):
        raise ValueError("Use a direct HTTPS image URL ending in .png, .jpg, .webp or .gif.")
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
              instance_3=None, roster_announce=None, softres_url=None,
              custom_title=None, banner_url=None):
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
    # None preserves the title when older /raid setup workflows edit an event.
    # An explicitly blank title from the poster removes the optional suffix.
    with database(path) as con:
        if custom_title is None:
            existing = con.execute(
                "SELECT custom_title FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            custom_title = existing[0] if existing and existing[0] else ""
        if banner_url is None:
            existing_banner = con.execute(
                "SELECT banner_url FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            banner_url = existing_banner[0] if existing_banner else None
        banner_url = validate_banner_url(banner_url)
        if not isinstance(custom_title, str):
            raise ValueError("Optional title must be text.")
        custom_title = " ".join(custom_title.split())
        if len(custom_title) > 70:
            raise ValueError("Optional raid title must be 70 characters or less.")
        full_title = f"<Hyped> Raid #{number}"
        if custom_title:
            full_title += f" - {custom_title}"
        con.execute("""
            INSERT INTO raids(raid_id, title, raid_at, instance_1, instance_2,
                              instance_3, roster_announce_at, softres_url,
                              signup_open, custom_title, banner_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(raid_id) DO UPDATE SET
                title=excluded.title, raid_at=excluded.raid_at,
                instance_1=excluded.instance_1, instance_2=excluded.instance_2,
                instance_3=excluded.instance_3,
                roster_announce_at=excluded.roster_announce_at,
                softres_url=COALESCE(excluded.softres_url, raids.softres_url),
                custom_title=excluded.custom_title,
                banner_url=excluded.banner_url, signup_open=1
        """, (number, full_title, start.isoformat(),
              instances[0], instances[1] or None, instances[2] or None,
              announce.isoformat() if announce else None,
              softres, custom_title, banner_url))
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


def _split_from_tokens(tokens):
    """New diamonds and historic Group A/B Sheet values map to two Barrows teams."""
    if {"barrows 🔹", "group b", "🔹", "barrows b"} & tokens:
        return "🔹"
    return "🔸"  # Default Barrows group when a specific split is not supplied.


def roster_groups(event, public_roster):
    """Hyjal uses one 20-player roster; Barrows retains two diamond teams.

    Only leadership-approved names are passed in from the Sheet. One raider
    may appear in the Hyjal roster and one Barrows team on the same raid night.
    """
    instances = [
        (event.get(key) or "").casefold()
        for key in ("instance_1", "instance_2", "instance_3")
    ]
    has_hyjal = any("hyjal" in name for name in instances)
    has_barrows = any("barrows" in name for name in instances)
    has_ony = any("ony" in name for name in instances)

    group_titles = []
    if has_hyjal:
        group_titles.append("Hyjal · 20-player")
    if has_barrows:
        group_titles.extend(("Barrows · 10-player 🔸", "Barrows · 10-player 🔹"))
    if has_ony:
        group_titles.append("Ony · 40-player")
    if not group_titles:
        group_titles.append("Selected players")

    groups = {title: [] for title in group_titles}
    pending = []
    for name, assignment in public_roster:
        clean_name = discord.utils.escape_mentions(
            discord.utils.escape_markdown(name)
        )
        if not clean_name:
            continue
        tokens = {item.strip().casefold() for item in (assignment or "").split(",")}
        split = _split_from_tokens(tokens)
        matched = False
        if has_hyjal and "hyjal" in tokens:
            groups["Hyjal · 20-player"].append(clean_name)
            matched = True
        if has_barrows and (
            {"barrows 🔸", "barrows 🔹", "barrows a", "barrows b",
             "group a", "group b", "🔸", "🔹"} & tokens
        ):
            groups[f"Barrows · 10-player {split}"].append(clean_name)
            matched = True
        if has_ony and "ony" in tokens:
            groups["Ony · 40-player"].append(clean_name)
            matched = True
        if "Selected players" in groups:
            groups["Selected players"].append(clean_name)
            matched = True
        if not matched:
            pending.append(clean_name)
    result = list(groups.items())
    if pending:
        result.append(("Selected · group pending", pending))
    return result


def raid_panel_when(value):
    """Two-line UK raid schedule with a fixed three-hour real-time duration."""
    start = datetime.fromisoformat(value).astimezone(UK)
    end = (start.astimezone(timezone.utc) + timedelta(hours=3)).astimezone(UK)
    return (
        f"{start:%A}, {start.day} {start:%B %Y}\n"
        f"{start:%H:%M} - {end:%H:%M}"
    )


def raid_panel_intro(event):
    """Compact header; raid times are UK, announcement timestamps localise."""
    number = event["raid_id"]
    # Escape only the user-provided suffix; keep the guild prefix stable.
    title = f"<Hyped> Raid #{number}"
    if event.get("custom_title"):
        suffix = discord.utils.escape_mentions(
            discord.utils.escape_markdown(event["custom_title"])
        )
        title += f" - {suffix}"
    instances = [
        discord.utils.escape_mentions(discord.utils.escape_markdown(event[key]))
        for key in ("instance_1", "instance_2", "instance_3")
        if event.get(key)
    ]
    softres = event.get("softres_url")
    softres_line = f"\n[SoftRes sign-up]({softres})" if softres else ""
    return (
        f"## {title}\n"
        f"{raid_panel_when(event['raid_at'])}\n\n"
        + "**Instances:** " + ", ".join(instances)
        + "\n**Check:** <#1563663258047619123>"
        + softres_line
        + "\n### Roster"
    )


def raid_roster_status(event):
    """Use real publication/change times, never infer publication from a plan."""
    stamp = event.get("roster_updated_at")
    if stamp:
        return "**Update:** " + format_discord_date(stamp, style="F", with_relative=False)
    stamp = event.get("roster_posted_at")
    if stamp:
        return "**Roster posted:** " + format_discord_date(
            stamp, style="F", with_relative=False
        )
    return "**Roster Announcement:** " + format_discord_date(
        event.get("roster_announce_at"), style="R", with_relative=False
    )


def _roster_group_text(title, players):
    """Short roster headings with consistent empty-state wording."""
    if title.startswith("Barrows · 10-player "):
        heading = ("**Barrows**\n" if title.endswith("🔸") else "") + title[-1]
    elif title == "Hyjal · 20-player":
        heading = "**Hyjal**"
    elif title == "Ony · 40-player":
        heading = "**Ony**"
    else:
        heading = f"**{title}**"
    if not players:
        if title == "Hyjal · 20-player":
            return heading + "\n🔹🔸 - TBD"
        if title.startswith("Barrows · 10-player "):
            return heading + " - TBD"
        return heading + "\n- TBD"
    names = []
    length = len(heading)
    for name in players:
        line = f"• {name}"
        if length + len(line) + 2 > 3800:
            names.append("-# *More selected players not shown here*")
            break
        names.append(line)
        length += len(line) + 1
    return heading + "\n" + "\n".join(names)


def raid_panel_text(event, public_roster=()):
    """Plain-text equivalent used for previews and roster layout tests."""
    groups = roster_groups(event, public_roster)
    return raid_panel_intro(event) + "".join(
        "\n\n" + _roster_group_text(title, players)
        for title, players in groups
    ) + "\n\n" + raid_roster_status(event)


def roster_components(event, public_roster):
    """Text-only roster groups, with no voice-link buttons."""
    return [
        discord.ui.TextDisplay(_roster_group_text(title, players))
        for title, players in roster_groups(event, public_roster)
    ]


class RaidEventPanel(discord.ui.LayoutView):
    """Real Discord V2 message, persistent across bot restarts."""
    def __init__(self, event, *, on_register=None, on_characters=None, on_signup=None,
                 on_gear=None, on_update=None, on_absent=None,
                 public_roster=()):
        super().__init__(timeout=None)
        event_id = event["raid_id"]
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(raid_panel_intro(event)),
                accessory=discord.ui.Thumbnail(
                    RAID_PANEL_IMAGE, description="Hyped raid image"
                ),
            ),
            *roster_components(event, public_roster),
            discord.ui.TextDisplay(raid_roster_status(event)),
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    media=event.get("banner_url") or RAID_PANEL_BANNER,
                    description="Hyped raid bottom banner",
                ),
            ),
            accent_color=RAID_PANEL_ACCENT,
        ))
        controls = discord.ui.ActionRow()
        sign_up = discord.ui.Button(
            label="Sign", custom_id=f"hyped_raid_{event_id}_signup",
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
