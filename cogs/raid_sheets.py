"""Hyped Ogre raid Sheets bridge.

The bot's own SQLite database is authoritative for registration, sign-ups and
absences. The existing private RM - Roster Builder Google tab is authoritative
for manually selected/visible roster rows, including PUGs.

Only RM - tabs are accessed. This integration is opt-in and requires a Google
service-account key shared with the workbook; ChatGPT's Drive connection is
not a credential that the Fly.io bot can use.
"""
import base64
import json
import os
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote

SPREADSHEET_ID = os.getenv("RAID_SHEETS_ID", "").strip()
API = "https://sheets.googleapis.com/v4/spreadsheets"
SHEETS = {
    "characters": ("RM - Characters", 10),
    "signups": ("RM - Sign-ups", 10),
    "roster": ("RM - Roster Builder", 15),
    "gear": ("RM - Gear Checks", 9),
    "history": ("RM - Raid History", 7),
}
READ_RANGES = {
    name: f"'{tab}'!A1:O2000" for name, (tab, _) in SHEETS.items()
}


def configured():
    return bool(SPREADSHEET_ID and (
        os.getenv("RAID_GOOGLE_SERVICE_ACCOUNT_B64")
        or os.getenv("RAID_GOOGLE_SERVICE_ACCOUNT_JSON")
    ))


def _session():
    """Use read/write Sheets scope only; never log credentials or API tokens."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    encoded = os.getenv("RAID_GOOGLE_SERVICE_ACCOUNT_B64")
    plain = os.getenv("RAID_GOOGLE_SERVICE_ACCOUNT_JSON")
    if not (encoded or plain):
        raise RuntimeError("RAID_GOOGLE_SERVICE_ACCOUNT_B64 not set.")
    try:
        raw = base64.b64decode(encoded, validate=True).decode("utf-8") if encoded else plain
        info = json.loads(raw)
        if info.get("type") != "service_account":
            raise ValueError("Credential must be a Google service account.")
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    except (ValueError, KeyError, UnicodeError) as exc:
        raise RuntimeError("Invalid raid Google service-account credential.") from exc
    return AuthorizedSession(credentials)


def _json(session, method, endpoint, **kwargs):
    response = session.request(method, endpoint, timeout=20, **kwargs)
    try:
        response.raise_for_status()
    except Exception as exc:
        # Avoid echoing authentication errors or spreadsheet contents in logs.
        raise RuntimeError(f"Google Sheets returned HTTP {response.status_code}.") from exc
    return response.json()


def _read_tabs(session):
    data = _json(
        session, "GET", f"{API}/{SPREADSHEET_ID}/values:batchGet",
        params=[("ranges", r) for r in READ_RANGES.values()]
        + [("valueRenderOption", "FORMATTED_VALUE")],
    )
    blocks = data.get("valueRanges", [])
    if len(blocks) != len(READ_RANGES):
        raise RuntimeError("Google Sheets did not return every RM tab.")
    return {name: block.get("values", [])
            for name, block in zip(READ_RANGES, blocks)}


def _date_iso(iso):
    return iso[:10] if iso else ""


def _raid_snapshot(db_path):
    """Never expose SimC raw exports or leadership-only attendance in Sheets."""
    from cogs.raid_schedule import database

    with database(db_path) as con:
        con.row_factory = sqlite3.Row
        characters = [dict(r) for r in con.execute(
            "SELECT * FROM characters ORDER BY character_id"
        )]
        raids = {r["raid_id"]: dict(r) for r in con.execute(
            "SELECT * FROM raids ORDER BY raid_id"
        )}
        signups = [dict(r) for r in con.execute("""
            SELECT s.raid_id, s.discord_id, s.character_id, s.availability,
                   s.note, s.submitted_at, c.name AS character,
                   c.class_name, c.spec, c.role
            FROM signups s JOIN characters c ON c.character_id=s.character_id
            ORDER BY s.raid_id, s.discord_id
        """)]
        absences = [dict(r) for r in con.execute(
            "SELECT * FROM raid_absences ORDER BY raid_id, discord_id"
        )]
        gear = [dict(r) for r in con.execute("""
            SELECT c.character_id, c.discord_id, c.name,
                (SELECT MAX(g.submitted_at) FROM gear_exports g
                 WHERE g.character_id=c.character_id) AS simc_latest,
                (SELECT MAX(i.submitted_at) FROM gear_images i
                 WHERE i.character_id=c.character_id) AS image_latest
            FROM characters c
            WHERE EXISTS (SELECT 1 FROM gear_exports g
                          WHERE g.character_id=c.character_id)
               OR EXISTS (SELECT 1 FROM gear_images i
                          WHERE i.character_id=c.character_id)
        """)]
    return characters, raids, signups, absences, gear


def _desired_rows(db_path):
    chars, raids, signups, absences, gear = _raid_snapshot(db_path)
    by_raid_member = {(r["raid_id"], r["discord_id"]): r for r in signups}
    off = {(r["raid_id"], r["discord_id"]): r for r in absences}
    desired = {
        "characters": {},
        "signups": {},
        "roster": {},
        "gear": {},
        "history": {},
    }

    for c in chars:
        # Legacy realm is intentionally excluded; the game is realmless.
        key = str(c["character_id"])
        simc_date = (
            datetime.fromtimestamp(c["last_simc_at"], timezone.utc).isoformat()
            if c.get("last_simc_at") else ""
        )
        desired["characters"][key] = [
            key, str(c["discord_id"]), "", c["name"], c["class_name"],
            c["spec"], c["role"], "Main" if c["is_main"] else "Alt",
            "", simc_date,
        ]

    for r in raids.values():
        key = str(r["raid_id"])
        desired["history"][key] = [
            key, _date_iso(r["raid_at"]),
            ", ".join(x for x in (r.get("instance_1"), r.get("instance_2"),
                                    r.get("instance_3")) if x),
            r["raid_at"],  # Bot-owned columns A:D only.
        ]

    for key_pair in sorted(set(by_raid_member) | set(off)):
        raid_id, member_id = key_pair
        raid = raids.get(raid_id)
        if not raid:
            continue
        signup = by_raid_member.get(key_pair)
        absence = off.get(key_pair)
        character_id = str(signup["character_id"]) if signup else ""
        character = signup["character"] if signup else ""
        availability = ("Unavailable" if absence else signup["availability"]
                        if signup else "Unavailable")
        note = (absence["note"] if absence else signup["note"] if signup else "") or ""
        submitted = signup["submitted_at"] if signup else ""
        sign_off_time = absence["signed_off_at"] if absence else ""
        key = f"{raid_id}:{member_id}"
        desired["signups"][key] = [
            str(raid_id), _date_iso(raid["raid_at"]), str(member_id),
            character_id, character, availability, note, submitted,
            sign_off_time or submitted, sign_off_time,
        ]
        if signup:
            desired["roster"][key] = [
                str(raid_id), _date_iso(raid["raid_at"]), str(member_id),
                character_id, character, signup["class_name"],
                signup["spec"], signup["role"],
            ]
    for g in gear:
        key = str(g["character_id"])
        latest = max(g["simc_latest"] or "", g["image_latest"] or "")
        formats = ("SimC + Screenshot" if g["simc_latest"] and g["image_latest"]
                   else "SimC" if g["simc_latest"] else "Screenshot")
        desired["gear"][key] = [
            key, str(g["discord_id"]), g["name"], latest, formats,
        ]
    return desired


def _existing_keys(kind, rows):
    """Return key -> sheet row (1-based), skipping leader-entered PUGs."""
    found = {}
    for number, row in enumerate(rows[1:], 2):
        if kind in ("characters", "gear"):
            key = row[0].strip() if row else ""
        elif kind == "history":
            key = row[0].strip() if row else ""
        else:
            key = f"{row[0]}:{row[2]}" if len(row) > 2 and row[0] and row[2] else ""
        if key and key not in found:
            found[key] = number
    return found


def _sync_rows(session, kind, rows, desired):
    tab, _ = SHEETS[kind]
    existing = _existing_keys(kind, rows)
    write_width = {"characters": 10, "signups": 10, "roster": 8,
                   "gear": 5, "history": 4}[kind]
    end_col = {10: "J", 8: "H", 5: "E", 4: "D"}[write_width]
    updates, additions = [], []
    for key, values in desired.items():
        if key in existing:
            sheet_row = existing[key]
            old = rows[sheet_row - 1] if sheet_row - 1 < len(rows) else []
            old = (old[:write_width] + [""] * write_width)[:write_width]
            if old != values:
                updates.append({
                    "range": f"'{tab}'!A{sheet_row}:{end_col}{sheet_row}",
                    "values": [values],
                })
        else:
            additions.append(values)
    # Do not clear/delete rows. All manual columns I:O in roster are untouched.
    for start in range(0, len(updates), 100):
        _json(session, "POST", f"{API}/{SPREADSHEET_ID}/values:batchUpdate",
              json={"valueInputOption": "RAW",
                    "data": updates[start:start + 100]})
    if additions:
        _json(session, "POST", f"{API}/{SPREADSHEET_ID}/values/"
              + quote(f"'{tab}'!A:{end_col}", safe="'!:") + ":append",
              params={"valueInputOption": "RAW",
                      "insertDataOption": "INSERT_ROWS"},
              json={"values": additions})
    return len(updates) + len(additions)


def _public_rows(rows, *, raid_dates=None, unavailable=()):
    """Reveal only Selected + Yes on active raid dates, excluding absent users.

    K = Show on Discord, I = Selection; L:N = per-instance assignments.
    PUGs can be entered directly into roster sheet without Discord IDs.
    """
    result = {}
    unavailable = set(unavailable)
    for row in rows[1:]:
        fields = (row + [""] * 15)[:15]
        raid_id, raid_date, discord_id, _, name, _, _, _, selection, _, show, ony, hyjal, barrows, _ = fields
        if not raid_id.isdecimal() or not name.strip():
            continue
        if raid_dates is not None and raid_dates.get(int(raid_id)) != raid_date:
            continue
        if discord_id and f"{raid_id}:{discord_id}" in unavailable:
            continue
        if selection.strip().casefold() != "selected" or show.strip().casefold() != "yes":
            continue
        assignments = []
        if ony.casefold() == "selected":
            assignments.append("Ony")
        if hyjal.casefold() == "selected":
            assignments.append("Hyjal")
        split = barrows.strip().casefold()
        if split in ("group a", "barrows a", "🔸"):
            assignments.append("Barrows 🔸")
        elif split in ("group b", "barrows b", "🔹"):
            assignments.append("Barrows 🔹")
        key = int(raid_id)
        roster = result.setdefault(key, [])
        if len(roster) < 60:
            roster.append((name.strip()[:80], ", ".join(assignments)))
    return result


def _save_public(db_path, published):
    """Mirror only explicit reveal flags; make no disclosure on sheet failure."""
    from cogs.raid_schedule import database
    changed = set()
    with database(db_path) as con:
        raid_ids = [r[0] for r in con.execute("SELECT raid_id FROM raids")]
        for raid_id in raid_ids:
            new = published.get(raid_id, [])
            current = con.execute(
                "SELECT name, assignment FROM raid_public_roster "
                "WHERE raid_id=? ORDER BY position", (raid_id,)
            ).fetchall()
            if current == new:
                continue
            con.execute("DELETE FROM raid_public_roster WHERE raid_id=?", (raid_id,))
            con.executemany(
                "INSERT INTO raid_public_roster "
                "(raid_id, position, name, assignment) VALUES (?, ?, ?, ?)",
                [(raid_id, i, name, assignment)
                 for i, (name, assignment) in enumerate(new)],
            )
            changed.add(raid_id)
    return changed


def sync_once(db_path):
    """Synchronise one SQLite snapshot, then ingest manually approved roster.

    No external connection is attempted unless the Fly secret is configured.
    This function is blocking; use asyncio.to_thread from discord.py.
    """
    if not configured():
        raise RuntimeError("Raid Google Sheets credentials are not configured.")
    session = _session()
    try:
        remote = _read_tabs(session)
        desired = _desired_rows(db_path)
        counts = {}
        for kind in SHEETS:
            counts[kind] = _sync_rows(session, kind, remote[kind], desired[kind])
        # Re-read the manually editable roster only *after* appends/updates.
        # Never overwrite I:O: they belong entirely to raid leadership.
        roster = _json(
            session, "GET", f"{API}/{SPREADSHEET_ID}/values/"
            + quote("'RM - Roster Builder'!A1:O2000", safe="'!:"),
            params={"valueRenderOption": "FORMATTED_VALUE"},
        ).get("values", [])
        _, raids, _, _, _ = _raid_snapshot(db_path)
        raid_dates = {number: _date_iso(event["raid_at"])
                      for number, event in raids.items()}
        unavailable = {key for key, values in desired["signups"].items()
                       if values[5] == "Unavailable"}
        changed = _save_public(
            db_path, _public_rows(
                roster, raid_dates=raid_dates, unavailable=unavailable
            )
        )
        return counts, changed
    finally:
        session.close()
