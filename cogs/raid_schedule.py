"""Raid schedule storage and public-facing Components V2 raid panel.

An event is configured in the private testing channel for now. Sheets-to-bot
synchronisation is separate: the Google Sheet does not authorise a Fly bot.
"""
import sqlite3
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import discord
from cogs.emojis import markup as guild_emoji, combat_role
from cogs.forever_role import ACCENT as LEADERSHIP_ACCENT

UK = ZoneInfo("Europe/London")
RAID_IMAGE = "https://i.vgy.me/XIszIH.png"  # Poster thumbnail stays unchanged.
RAID_PANEL_IMAGE = "https://i.vgy.me/asI88C.png"
RAID_PANEL_BANNER = "https://i.vgy.me/y6bCxy.png"
ACCENT = 0xA87535  # Keep the private raid poster setup colour.
RAID_PANEL_ACCENT = LEADERSHIP_ACCENT
DEFAULT_CHECK_CHANNEL_ID = 1563663258047619123
_UNCHANGED = object()  # Older /raid setup calls must keep poster-specific settings.
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
    "end_at": "TEXT",
    "check_channel_id": "INTEGER",  # Old setting retained for migration.
    "check_message_url": "TEXT",
    "roster_posted_at": "TEXT",
    "roster_updated_at": "TEXT",
    "confirmed_at": "TEXT",
    "archived_at": "TEXT",
    "archive_notes": "TEXT",
    "wcl_url": "TEXT",
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


def validate_check_message_url(value):
    """A shortened Check link must point to a Discord message."""
    if not value or not value.strip():
        return None
    url = value.strip()
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "discord.com"
            or parts.username or parts.password or parts.port
            or not re.fullmatch(r"/channels/\d{16,20}/\d{16,20}/\d{16,20}/?", parts.path)):
        raise ValueError("Use a Discord message permalink, not a channel link.")
    return "https://discord.com" + parts.path.rstrip("/")


def ensure_raid_fields(path):
    """Add new columns without clearing any existing sign-ups or characters."""
    with database(path) as con:
        names = {row[1] for row in con.execute("PRAGMA table_info(raids)")}
        if not names:
            raise RuntimeError("Raid schema must be created before schedule migration.")
        for field, sql_type in RAID_COLUMNS.items():
            if field not in names:
                con.execute(f"ALTER TABLE raids ADD COLUMN {field} {sql_type}")
        con.execute("""
            CREATE TABLE IF NOT EXISTS raid_confirmed_roster (
                raid_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                discord_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                assignment TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (raid_id, position)
            )
        """)
        for table in ("raid_confirmed_roster", "raid_public_roster"):
            existing_columns = {
                row[1] for row in con.execute(f"PRAGMA table_info({table})")
            }
            for column in ("class_name", "role", "spec"):
                if column not in existing_columns:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


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
              custom_title=None, banner_url=None,
              end_at=_UNCHANGED, check_channel_id=_UNCHANGED,
              check_message_url=_UNCHANGED):
    softres = validate_softres_url(softres_url)
    if not 1 <= number <= 100000:
        raise ValueError("Raid number must be between 1 and 100000.")
    start = parse_uk_datetime(raid_date)
    if end_at is not _UNCHANGED and end_at is not None:
        if isinstance(end_at, str):
            end = datetime.fromisoformat(end_at)
        elif isinstance(end_at, datetime):
            end = end_at
        else:
            raise ValueError("Raid end time must be a datetime.")
        if end.tzinfo is None:
            raise ValueError("Raid end time must include a timezone.")
        seconds = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
        if not 0 < seconds <= 12 * 3600:
            raise ValueError("Raid end time must be after the start, within 12 hours.")
        end_at = end.astimezone(UK).isoformat()
    if check_channel_id is not _UNCHANGED:
        if isinstance(check_channel_id, bool) or not str(check_channel_id).isdigit():
            raise ValueError("Choose a valid Discord text channel for Check.")
        check_channel_id = int(check_channel_id)
        if not 0 < check_channel_id < 2**64:
            raise ValueError("Choose a valid Discord text channel for Check.")
    if check_message_url is not _UNCHANGED:
        check_message_url = validate_check_message_url(check_message_url)
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
        existing_state = con.execute(
            "SELECT roster_published, archived_at FROM raids WHERE raid_id=?",
            (number,)
        ).fetchone()
        if existing_state and existing_state[1]:
            raise ValueError("Archived raids are read-only.")
        if existing_state and existing_state[0]:
            raise ValueError("Reopen the confirmed roster before editing this raid.")
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
        if end_at is _UNCHANGED:
            current = con.execute(
                "SELECT raid_at, end_at FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            end_at = current[1] if current else None
            if end_at and current[0] != start.isoformat():
                # Legacy start-time edits keep the same custom raid duration,
                # rather than pointing to an end on the old calendar date.
                original_start = datetime.fromisoformat(current[0])
                original_end = datetime.fromisoformat(end_at)
                duration = (original_end.astimezone(timezone.utc) -
                            original_start.astimezone(timezone.utc))
                end_at = (start.astimezone(timezone.utc) +
                          duration).astimezone(UK).isoformat()
        if check_channel_id is _UNCHANGED:
            current = con.execute(
                "SELECT check_channel_id FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            check_channel_id = current[0] if current else None
        if check_message_url is _UNCHANGED:
            previous_check = con.execute(
                "SELECT check_message_url FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            check_message_url = previous_check[0] if previous_check else None
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
                              signup_open, custom_title, banner_url,
                              end_at, check_channel_id, check_message_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(raid_id) DO UPDATE SET
                title=excluded.title, raid_at=excluded.raid_at,
                instance_1=excluded.instance_1, instance_2=excluded.instance_2,
                instance_3=excluded.instance_3,
                roster_announce_at=excluded.roster_announce_at,
                softres_url=COALESCE(excluded.softres_url, raids.softres_url),
                custom_title=excluded.custom_title,
                banner_url=excluded.banner_url,
                end_at=excluded.end_at,
                check_channel_id=excluded.check_channel_id,
                check_message_url=excluded.check_message_url,
                signup_open=CASE WHEN raids.roster_published=1
                       OR raids.archived_at IS NOT NULL
                       THEN 0 ELSE 1 END
        """, (number, full_title, start.isoformat(),
              instances[0], instances[1] or None, instances[2] or None,
              announce.isoformat() if announce else None,
              softres, custom_title, banner_url, end_at,
              check_channel_id, check_message_url))
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
        con.execute("DELETE FROM raid_confirmed_roster WHERE raid_id=?", (number,))
        con.execute("DELETE FROM attendance WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raids WHERE raid_id=?", (number,))
    return True

def raid_status(path, number, mode, *, confirmed_entries=(), notes=None, wcl_url=None):
    """Explicit leadership lifecycle: confirm, reopen, archive; no auto-confirm."""
    if mode not in ("confirm", "reopen", "archive"):
        raise ValueError("Unknown raid status transition.")
    now = datetime.now(timezone.utc).isoformat()
    with database(path) as con:
        con.execute("PRAGMA foreign_keys=ON")
        event = con.execute(
            "SELECT roster_published, archived_at FROM raids WHERE raid_id=?",
            (number,)
        ).fetchone()
        if not event:
            raise ValueError("Raid does not exist.")
        if event[1]:
            raise ValueError("Archived raids are read-only.")
        if mode == "confirm":
            if event[0]:
                raise ValueError("Roster is already confirmed.")
            if not confirmed_entries:
                raise ValueError("Select and show at least one player before confirming.")
            con.execute("DELETE FROM raid_confirmed_roster WHERE raid_id=?", (number,))
            con.executemany(
                "INSERT INTO raid_confirmed_roster "
                "(raid_id, position, discord_id, name, assignment, class_name, role, spec) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [(number, index, str(entry[0] or ""), entry[1], entry[2],
                  *tuple(entry[3:6]) + ("",) * (3 - len(entry[3:6])))
                 for index, entry in enumerate(confirmed_entries)],
            )
            con.execute(
                "UPDATE raids SET roster_published=1, signup_open=0, "
                "confirmed_at=?, roster_posted_at=?, roster_updated_at=NULL "
                "WHERE raid_id=?", (now, now, number)
            )
        elif mode == "reopen":
            con.execute(
                "UPDATE raids SET roster_published=0, signup_open=1, "
                "confirmed_at=NULL, roster_posted_at=NULL, roster_updated_at=NULL "
                "WHERE raid_id=?", (number,)
            )
            con.execute("DELETE FROM raid_confirmed_roster WHERE raid_id=?", (number,))
        else:
            if not event[0]:
                raise ValueError("Confirm the raid roster before archiving.")
            con.execute(
                "UPDATE raids SET archived_at=?, archive_notes=?, wcl_url=?, "
                "signup_open=0 WHERE raid_id=?",
                (now, notes or "", wcl_url or "", number),
            )
    return read_raid(path, number)


def confirmed_roster(path, number, unavailable=(), *, include_details=False):
    """Public snapshot is locked; only subsequent absences can remove members."""
    unavailable = {str(value) for value in unavailable}
    with database(path) as con:
        rows = con.execute(
            "SELECT discord_id, name, assignment, class_name, role, spec "
            "FROM raid_confirmed_roster WHERE raid_id=? ORDER BY position", (number,)
        ).fetchall()
    return [(name, assignment, klass or "", role or "", spec or "")
            if include_details else (name, assignment)
            for member_id, name, assignment, klass, role, spec in rows
            if not member_id or member_id not in unavailable]


def read_public_roster(path, raid_id, *, include_details=False):
    """Only selected, visible names; optional class/role metadata for emojis."""
    with database(path) as con:
        columns = ("name, assignment, class_name, role, spec"
                   if include_details else "name, assignment")
        return con.execute(
            f"SELECT {columns} FROM raid_public_roster "
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


def roster_name_with_emojis(name, klass="", role="", spec="", emoji_map=None):
    """Show fixed custom Discord emoji markup; preserve existing API."""
    name = discord.utils.escape_mentions(discord.utils.escape_markdown(name))
    if not name:
        return ""
    klass = (klass or "").strip().title()
    role = combat_role((role or "").strip().title(), klass, spec)
    return " ".join(part for part in (guild_emoji(klass), name) if part)


def roster_groups(event, public_roster, *, emoji_map=None, by_role=False):
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
    for entry in public_roster:
        name, assignment = entry[:2]
        klass, role, spec = (tuple(entry[2:5]) + ("", "", ""))[:3]
        clean_name = roster_name_with_emojis(
            name, klass, role, spec, emoji_map=emoji_map
        )
        if not clean_name:
            continue
        if by_role:
            # All damage specs share the public DPS category. Missing historical
            # metadata stays visible under DPS rather than being dropped.
            role_group = (role or "").strip().casefold()
            role_group = ("Tank" if role_group == "tank"
                          else "Healer" if role_group in ("healer", "heal")
                          else "DPS")
            clean_name = (role_group, clean_name)
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


def raid_panel_when(value, end_at=None):
    """Compact UK date, live Discord countdown and local start/end times."""
    start = datetime.fromisoformat(value).astimezone(UK)
    end = (datetime.fromisoformat(end_at).astimezone(UK) if end_at else
           (start.astimezone(timezone.utc) + timedelta(hours=3)).astimezone(UK))
    day = start.day
    suffix = ("th" if 11 <= day % 100 <= 13
              else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))
    short_date = f"{start:%A} {day}{suffix} {start:%b}"
    return (
        f"{short_date} - {format_discord_date(start.isoformat(), style='R', with_relative=False)}\n"
        f"{format_discord_date(start.isoformat(), style='t', with_relative=False)}"
        f" - {format_discord_date(end.isoformat(), style='t', with_relative=False)}"
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
        f"{raid_panel_when(event['raid_at'], event.get('end_at'))}\n\n"
        + "**Instances:** " + ", ".join(instances)
        + (f"\n**Check:** [View message]({event['check_message_url']})"
           if event.get("check_message_url") else "")
        + softres_line
    )


def raid_roster_status(event):
    """Only an explicit leadership confirmation is a published roster."""
    if event.get("archived_at"):
        return "**Archived:** " + format_discord_date(
            event["archived_at"], style="F", with_relative=False
        )
    if not event.get("roster_published"):
        return "**Roster confirmation:** " + format_discord_date(
            event.get("roster_announce_at"), style="R", with_relative=False
        )
    stamp = event.get("roster_updated_at")
    if stamp:
        return "**Update:** " + format_discord_date(stamp, style="F", with_relative=False)
    stamp = event.get("roster_posted_at")
    if stamp:
        return "**Roster confirmed:** " + format_discord_date(
            stamp, style="F", with_relative=False
        )
    return "**Roster confirmed:** To be confirmed"


def _roster_group_text(title, players):
    """Always show muted Tanks / Healers / DPS placeholders per raid roster."""
    if title.startswith("Barrows · 10-player "):
        heading = f"**Barrows** {title[-1]}"
    elif title == "Hyjal · 20-player":
        heading = "**Hyjal** 🔹🔸"
    elif title == "Ony · 40-player":
        heading = "**Ony**"
    else:
        heading = f"**{title}**"

    lines = [heading]
    for label, category in (("Tanks", "Tank"), ("Healers", "Healer"), ("DPS", "DPS")):
        lines.append(f"-# **{label}**")
        if players:
            matching = [
                name for player_role, name in players
                if player_role == category
            ] if all(isinstance(item, tuple) for item in players) else (
                players if category == "DPS" else []
            )
            lines.extend(matching)
    return "\n".join(lines)


def raid_panel_text(event, public_roster=(), *, emoji_map=None):
    """Plain-text equivalent used for previews and roster layout tests."""
    groups = roster_groups(event, public_roster, emoji_map=emoji_map, by_role=True)
    return raid_panel_intro(event) + ("\n### Roster" if public_roster else "") + "".join(
        "\n\n" + _roster_group_text(title, players)
        for title, players in groups
    ) * bool(public_roster) + "\n\n" + raid_roster_status(event)


def roster_components(event, public_roster, *, emoji_map=None):
    """Text-only roster groups, with no voice-link buttons."""
    return [
        discord.ui.TextDisplay(_roster_group_text(title, players))
        for title, players in roster_groups(event, public_roster, emoji_map=emoji_map, by_role=True)
    ] if public_roster else []


class RaidEventPanel(discord.ui.LayoutView):
    """Real Discord V2 message, persistent across bot restarts."""
    def __init__(self, event, *, on_register=None, on_characters=None, on_signup=None,
                 on_gear=None, on_update=None, on_absent=None,
                 public_roster=(), emoji_map=None):
        super().__init__(timeout=None)
        event_id = event["raid_id"]
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(raid_panel_intro(event)),
                accessory=discord.ui.Thumbnail(
                    RAID_PANEL_IMAGE, description="Hyped raid image"
                ),
            ),
            *( [discord.ui.TextDisplay("### Roster")] + roster_components(event, public_roster, emoji_map=emoji_map) if public_roster else [] ),
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
            label="Attend", custom_id=f"hyped_raid_{event_id}_signup",
            style=discord.ButtonStyle.success,
            disabled=not bool(event.get("signup_open")) or bool(event.get("roster_published")),
        )
        update = discord.ui.Button(
            label="Characters", custom_id=f"hyped_raid_{event_id}_update",
            style=discord.ButtonStyle.secondary,
            disabled=(on_update is None or bool(event.get("roster_published"))
                      or bool(event.get("archived_at"))),
        )
        gear = discord.ui.Button(
            label="SimC", custom_id=f"hyped_raid_{event_id}_gear",
            style=discord.ButtonStyle.secondary,
            disabled=on_gear is None or bool(event.get("archived_at")),
        )
        absent = discord.ui.Button(
            label="Absent", custom_id=f"hyped_raid_{event_id}_absent",
            style=discord.ButtonStyle.danger,
            disabled=on_absent is None or bool(event.get("archived_at")),
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
        for control in (sign_up, update, gear):
            controls.add_item(control)
        self.add_item(controls)
