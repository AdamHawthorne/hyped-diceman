"""One private Google Sheets tab per Hyped raid.

The raid-only SQLite DB owns registration and availability. Leadership owns
selection/visibility/instance choices keyed by Discord ID in each raid tab.
The old RM tabs are intentionally left intact as read-only migration backups.
"""
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from cogs import raid_sheets
from cogs.forever_role import ACCENT as LEADERSHIP_ACCENT

TITLE_COLOR = {key: ((LEADERSHIP_ACCENT >> shift) & 255) / 255
               for key, shift in (("red", 16), ("green", 8), ("blue", 0))}
from cogs.raid_schedule import confirmed_roster, database, read_raid, read_signup_roster

UK = ZoneInfo("Europe/London")
COLUMNS = (
    "Discord Name", "Character", "Class", "Spec", "Role", "Main / Alt",
    "Sign-up Notes", "Signed At", "Status", "Selection", "Show on Discord",
    "Hyjal", "Barrows", "Ony", "Leadership Notes", "Gear / SimC",
    "Discord ID (internal)", "Character ID (internal)", "Absent At"
)
SECTIONS = ("Tanks", "Healers", "Melee", "Ranged", "Absent")
MELEE = {
    ("warrior", "*"), ("rogue", "*"), ("paladin", "retribution"),
    ("druid", "feral"), ("shaman", "enhancement"),
    ("hunter", "survival"),
}
VALUES = {
    9: ("Pending", "Selected", "Bench", "Not selected"),
    10: ("No", "Yes"),
    11: ("No", "Yes"),
    12: ("No", "🔸", "🔹"),
    13: ("No", "Yes"),
}
MAX_BODY_ROWS = 460

# Re-apply row styling on the first sync after a bot restart, and whenever
# a role section moves as more people register or change role.
_FORMATTED_SECTION_LAYOUTS = {}


# Absent At is displayed in R; Signed At remains the far-right column S.
DISPLAY_ORDER = (0, 1, 2, 3, 4, 5, 6, 8, 9, 11, 12, 13, 10, 15, 14, 16, 17, 18, 7)


def _uk_timestamp(value):
    """Friendly UK time display; SQLite retains original event timestamps."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UK)
        return dt.astimezone(UK).strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return str(value)


def _display(row):
    padded = (list(row) + [""] * len(COLUMNS))[:len(COLUMNS)]
    for index, label in ((7, "Signed At"), (18, "Absent At")):
        if padded[index] != label:
            padded[index] = _uk_timestamp(padded[index])
    return [padded[i] for i in DISPLAY_ORDER]


def _internal(row):
    padded = (list(row) + [""] * len(COLUMNS))[:len(COLUMNS)]
    restored = [""] * len(COLUMNS)
    for display_index, internal_index in enumerate(DISPLAY_ORDER):
        restored[internal_index] = padded[display_index]
    return restored



def tab_title(event):
    start = datetime.fromisoformat(event["raid_at"]).astimezone(UK)
    return f"Raid #{event['raid_id']} - {start:%a %d %b %Y}"


def _q(tab):
    return quote(f"'{tab}'!A1:S500", safe="'!:")


def _read(session, tab):
    result = raid_sheets._json(
        session, "GET", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/{_q(tab)}",
        params={"valueRenderOption": "FORMATTED_VALUE"},
    ).get("values", [])
    return [_internal(row) for row in result]


def _list_tabs(session):
    result = raid_sheets._json(
        session, "GET", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}",
        params={"fields": "sheets(properties(sheetId,title,hidden,gridProperties(columnCount)))"},
    )
    return {
        sheet["properties"]["title"]: sheet["properties"]
        for sheet in result.get("sheets", [])
    }


def _group(signup, absent):
    if absent:
        return "Absent"
    role = (signup.get("role") or "").casefold()
    if role == "tank":
        return "Tanks"
    if role == "healer":
        return "Healers"
    klass = (signup.get("class_name") or "").casefold()
    spec = (signup.get("spec") or "").casefold()
    if (klass, spec) in MELEE or (klass, "*") in MELEE:
        return "Melee"
    return "Ranged"


def _leadership(rows):
    """Read manual options by immutable Discord ID, not row position."""
    manual, guests = {}, []
    for source in rows[7:]:
        row = (source + [""] * len(COLUMNS))[:len(COLUMNS)]
        if not row[1].strip() or row[1].strip() == "Character":
            continue
        member_id = row[16].strip()
        if member_id.isdecimal():
            manual[member_id] = row
        elif row[0].strip() and row[8].strip() not in ("", "Status"):
            # Keep leadership-entered PUGs without a Discord ID.
            guests.append(row)
    return manual, guests


def _legacy_choices(session, event):
    """One-time migration from the original Roster Builder, without editing it."""
    try:
        data = raid_sheets._json(
            session, "GET", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/"
            + quote("'RM - Roster Builder'!A1:O2000", safe="'!:"),
            params={"valueRenderOption": "FORMATTED_VALUE"},
        ).get("values", [])
    except RuntimeError:
        return {}
    migrated = {}
    for source in data[1:]:
        row = (source + [""] * 15)[:15]
        if (row[0] != str(event["raid_id"]) or
                row[1] != event["raid_at"][:10]):
            continue
        if not row[2].isdigit():
            if row[4]:
                migrated.setdefault("_guests", []).append([
                    "PUG", row[4], row[5], row[6], row[7], "Guest",
                    "", "", "Signed", row[8] or "Pending",
                    row[10] or "No", "Yes" if row[12] == "Selected" else "No",
                    {"Group A": "🔸", "Group B": "🔹",
                     "🔸": "🔸", "🔹": "🔹"}.get(row[13], "No"),
                    "Yes" if row[11] == "Selected" else "No",
                    row[9], "Guest", "", "",
                ])
            continue
        migrated[row[2]] = {
            "selection": row[8] or "Pending", "show": row[10] or "No",
            "hyjal": "Yes" if row[12] == "Selected" else "No",
            "barrows": {"Group A": "🔸", "Group B": "🔹",
                        "🔸": "🔸", "🔹": "🔹"}.get(row[13], "No"),
            "ony": "Yes" if row[11] == "Selected" else "No",
            "notes": row[9],
        }
    return migrated


def _rows(db_path, event, names, old_rows=(), legacy=None):
    """Build sorted role sections and preserve leadership choices by member ID."""
    characters, _, signups, absences, gear = raid_sheets._raid_snapshot(db_path)
    chars = {str(item["character_id"]): item for item in characters}
    gear_by_char = {str(item["character_id"]): item for item in gear}
    absent_by_member = {
        str(r["discord_id"]): r for r in absences
        if r["raid_id"] == event["raid_id"]
    }
    manual, guests = _leadership(old_rows)
    legacy = legacy or {}
    guests.extend(legacy.get("_guests", []))
    groups = {section: [] for section in SECTIONS}
    registered = set()
    for signup in signups:
        if signup["raid_id"] != event["raid_id"]:
            continue
        member_id = str(signup["discord_id"])
        registered.add(member_id)
        char_id = str(signup["character_id"])
        profile = chars.get(char_id, {})
        previous = manual.get(member_id, [""] * len(COLUMNS))
        migrated = legacy.get(member_id, {})
        absence = absent_by_member.get(member_id)
        absent = bool(absence or signup["availability"] == "Unavailable")
        was_absent = previous[8] == "Absent"
        def choice(index, key, default):
            if absent or was_absent:
                return "Not selected" if index == 9 and absent else default
            return previous[index] or migrated.get(key) or default
        sign_note = signup.get("note") or ""
        if absence:
            if absence.get("note"):
                sign_note += (" | " if sign_note else "") + "Absent: " + absence["note"]
            if absence.get("signed_off_at"):
                sign_note += (" | " if sign_note else "") + "Signed off: " + absence["signed_off_at"]
        name = (names or {}).get(int(member_id)) or previous[0] or member_id
        uploaded = gear_by_char.get(char_id, {})
        gear_status = ("SimC + screenshot" if uploaded.get("simc_latest")
                       and uploaded.get("image_latest") else
                       "SimC received" if uploaded.get("simc_latest") else
                       "Screenshot received" if uploaded.get("image_latest")
                       else "Not submitted")
        row = [
            name, signup["character"], signup["class_name"], signup["spec"],
            signup["role"], "Main" if profile.get("is_main", 1) else "Alt",
            sign_note, signup["submitted_at"], "Absent" if absent else "Signed",
            choice(9, "selection", "Selected"),
            "No" if absent else choice(10, "show", "Yes" if event.get("default_show_discord", 1) else "No"),
            choice(11, "hyjal", "No"),
            choice(12, "barrows", "No"),
            choice(13, "ony", "No"),
            previous[14] or migrated.get("notes") or "",
            gear_status, member_id, char_id,
            (absence.get("signed_off_at") or "") if absence else "",
        ]
        groups[_group(signup, absent)].append(row)

    for member_id, absence in absent_by_member.items():
        if member_id in registered:
            continue
        previous = manual.get(member_id, [""] * len(COLUMNS))
        name = (names or {}).get(int(member_id)) or previous[0] or member_id
        groups["Absent"].append([
            name, previous[1], previous[2], previous[3], previous[4],
            previous[5], ("Absent: " + (absence.get("note") or "")
                          + " | Signed off: " + absence["signed_off_at"]),
            previous[7], "Absent", "Not selected", "No", "No", "No", "No",
            previous[14], previous[15], member_id, previous[17],
            absence.get("signed_off_at") or "",
        ])

    for guest in guests:
        guest[8] = guest[8] or "Signed"
        guest = (guest + [""] * len(COLUMNS))[:len(COLUMNS)]
        groups[_group({"role": guest[4], "class_name": guest[2],
                       "spec": guest[3]}, guest[8] == "Absent")].append(guest)

    section_rows = []
    for section in SECTIONS:
        section_rows.append([section] + [""] * (len(COLUMNS) - 1))
        ordered = sorted(
            groups[section], key=lambda row: (row[2].casefold(), row[1].casefold())
        )
        section_rows.extend(ordered)
        if section != SECTIONS[-1]:
            section_rows.append([""] * len(COLUMNS))
    if len(section_rows) > MAX_BODY_ROWS:
        raise RuntimeError("This raid exceeds the safe single-tab player capacity.")
    return section_rows


def _approved(rows, event, *, include_details=False):
    """Return only specifically selected and visible signed-on players."""
    entries = []
    instance_names = " ".join(
        (event.get(k) or "").lower() for k in
        ("instance_1", "instance_2", "instance_3")
    )
    has_hyjal = "hyjal" in instance_names
    has_barrows = "barrows" in instance_names
    has_ony = "ony" in instance_names
    any_known = has_hyjal or has_barrows or has_ony
    for source in rows[7:]:
        row = (source + [""] * len(COLUMNS))[:len(COLUMNS)]
        if (row[8].strip().casefold() != "signed" or
                row[9].strip().casefold() != "selected" or
                row[10].strip().casefold() != "yes" or not row[1].strip()):
            continue
        assignment = []
        if has_hyjal and row[11].strip().casefold() == "yes":
            assignment.append("Hyjal")
        if has_barrows and row[12].strip() in ("🔸", "🔹"):
            assignment.append("Barrows " + row[12].strip())
        if has_ony and row[13].strip().casefold() == "yes":
            assignment.append("Ony")
        if any_known and not assignment:
            continue
        if not assignment:
            assignment.append("Selected players")
        entry = (row[16].strip(), row[1].strip()[:80], ", ".join(assignment))
        if include_details:
            entry += (row[2].strip(), row[4].strip(), row[3].strip())
        entries.append(entry)
    return entries[:60]


def _header(event, body, tab):
    when = datetime.fromisoformat(event["raid_at"]).astimezone(UK)
    state = ("Archived" if event.get("archived_at") else
             "Confirmed · locked" if event.get("roster_published") else
             "Forming")
    instances = ", ".join(x for x in (
        event.get("instance_1"), event.get("instance_2"), event.get("instance_3")
    ) if x)
    signed = sum(r[8] == "Signed" for r in body if len(r) > 8)
    absent = sum(r[8] == "Absent" for r in body if len(r) > 8)
    title = "<Hyped> Raid #" + str(event["raid_id"])
    if event.get("custom_title"):
        title += " - " + event["custom_title"]
    channel = event.get("panel_channel_id")
    message = event.get("panel_message_id")
    panel_url = (f"https://discord.com/channels/{channel}/{message}"
                 if channel and message else "")
    lines = [
        [title],
        ["Date", when.strftime("%A, %d %B %Y"), "Status", state,
         "Start (UK)", when.strftime("%H:%M")],
        ["Instances", instances, "Confirmed",
         event.get("confirmed_at") or "", "Archived", event.get("archived_at") or ""],
        ["Warcraft Logs", event.get("wcl_url") or "", "Raid Notes",
         event.get("archive_notes") or ""],
        [f"{signed} signed · {absent} absent"],
        ["", ""],
        list(COLUMNS),
    ]
    return [(r + [""] * (len(COLUMNS) - len(r)))[:len(COLUMNS)]
            for r in lines + body]


def _make_tab(session, title):
    created = raid_sheets._json(
        session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
        json={"requests": [{"addSheet": {"properties": {
            "title": title, "gridProperties": {
                "rowCount": 501, "columnCount": len(COLUMNS),
                "frozenRowCount": 0, "frozenColumnCount": 0,
                "hideGridlines": True
            }
        }}}]},
    )
    sid = created["replies"][0]["addSheet"]["properties"]["sheetId"]
    # Apply the agreed Raid #1 theme once, when creating a new raid tab.
    # Existing tabs are never recoloured by the sign-up sync.
    black = {"red": 0, "green": 0, "blue": 0}
    white = {"red": 1, "green": 1, "blue": 1}
    grey = {"red": 0.89, "green": 0.89, "blue": 0.89}
    ink = {"red": 0.08, "green": 0.08, "blue": 0.08}

    def fill(first, last, background, foreground, bold=False, size=10):
        return {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": first,
                      "endRowIndex": last, "startColumnIndex": 0,
                      "endColumnIndex": 19},
            "cell": {"userEnteredFormat": {
                "backgroundColor": background,
                "textFormat": {"foregroundColor": foreground, "bold": bold,
                               "fontFamily": "Roboto", "fontSize": size}}},
            "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"}}

    requests = [
        fill(0, 500, black, white),
        fill(0, 1, black, white, True, 18),
        fill(6, 7, black, white, True, 11),
        fill(7, 500, grey, ink),
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": 15, "endIndex": 17},
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser"}},
    ]
    # Role headings retain AMOLED black while player rows stay light grey.
    # The sync only changes values and dropdowns, never formatting.
    requests.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sid, "startRowIndex": 7,
                    "endRowIndex": 500, "startColumnIndex": 0,
                    "endColumnIndex": 19}],
        "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                       "values": [{"userEnteredValue":
                           '=REGEXMATCH($A8,"^(Tanks|Healers|Melee|Ranged|Absent)$")'}]},
                        "format": {"backgroundColor": black,
                                   "textFormat": {"foregroundColor": white,
                                                  "bold": True}}}}}})
    class_colours = {
        "Warrior": "C79C6E", "Warlock": "8787ED", "Shaman": "0070DD",
        "Rogue": "FFF569", "Priest": "FFFFFF", "Paladin": "F58CBA",
        "Monk": "00FF96", "Mage": "40C7EB", "Hunter": "ABD473",
        "Druid": "FF7D0A", "Demon Hunter": "A330C9",
        "Death Knight": "C41F3B",
    }
    for name, hex_colour in class_colours.items():
        colour = {channel: int(hex_colour[pos:pos + 2], 16) / 255
                  for channel, pos in (("red", 0), ("green", 2), ("blue", 4))}
        requests.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 7,
                        "endRowIndex": 500, "startColumnIndex": 2,
                        "endColumnIndex": 3}],
            "booleanRule": {"condition": {"type": "TEXT_CONTAINS",
                           "values": [{"userEnteredValue": name}]},
                            "format": {"backgroundColor": colour}}}}})
    # Selected players have a light-green selection cell, not a green row.
    requests.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sid, "startRowIndex": 7,
                    "endRowIndex": 500, "startColumnIndex": 8,
                    "endColumnIndex": 9}],
        "booleanRule": {"condition": {"type": "TEXT_EQ",
                       "values": [{"userEnteredValue": "Selected"}]},
                        "format": {"backgroundColor": {
                            "red": .84313726, "green": .9372549,
                            "blue": .84313726},
                            "textFormat": {"foregroundColor": {
                                "red": .08, "green": .25, "blue": .12}}}}}}})
    for col, width in ((0, 125), (1, 210), (2, 120), (3, 130),
                       (4, 90), (5, 90), (6, 200), (7, 95),
                       (8, 110), (9, 95), (10, 95), (11, 75),
                       (12, 135), (13, 145), (14, 205),
                       (17, 195), (18, 195)):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": col, "endIndex": col + 1},
            "properties": {"pixelSize": width}, "fields": "pixelSize",
        }})
    for old_col, options in VALUES.items():
        col = DISPLAY_ORDER.index(old_col)
        requests.append({"setDataValidation": {
            "range": {"sheetId": sid, "startRowIndex": 7, "endRowIndex": 500,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                       "values": [{"userEnteredValue": value} for value in options]},
                     "strict": True, "showCustomUi": True}}})
    raid_sheets._json(session, "POST",
                     f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
                     json={"requests": requests})
    return sid


def _section_positions(rows):
    """Absolute zero-based Sheet row indices for role headings."""
    return tuple(
        index for index, row in enumerate(rows[7:], start=7)
        if row[0] in SECTIONS and not row[1].strip()
    )


def _paint_sections(session, sid, rows):
    """Move the dark role bands with their expanding player groups.

    Existing raid tabs can have manually applied fixed-position dark rows:
    without repainting, the third Tank can inherit a Healers header's white
    text on black and the relocated Healers heading becomes a grey player row.
    Only change background and text style; retain values, notes, dropdowns,
    conditional class colours, borders, widths and alignment.
    """
    black = {"red": 0, "green": 0, "blue": 0}
    white = {"red": 1, "green": 1, "blue": 1}
    grey = {"red": 0.89, "green": 0.89, "blue": 0.89}
    ink = {"red": 0.08, "green": 0.08, "blue": 0.08}

    def paint(first, last, background, foreground, *, bold=False, size=10):
        return {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": first,
                      "endRowIndex": last, "startColumnIndex": 0,
                      "endColumnIndex": len(COLUMNS)},
            "cell": {"userEnteredFormat": {
                "backgroundColor": background,
                "textFormat": {"foregroundColor": foreground, "bold": bold,
                               "fontFamily": "Roboto", "fontSize": size}}},
            "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"}}

    # Reset old role heading colours, including rows vacated after someone
    # switches role; then paint current headings at their actual row positions.
    requests = [paint(7, 500, grey, ink)]
    requests.extend(paint(index, index + 1, black, white, bold=True, size=11)
                    for index in _section_positions(rows))
    requests.append(
        {"setDataValidation": {"range": {
            "sheetId": sid, "startRowIndex": 7, "endRowIndex": 500,
            "startColumnIndex": 7, "endColumnIndex": 8}}}
    )
    # Leadership Notes (display column O) is free text, not a dropdown.
    requests.append({"setDataValidation": {"range": {
        "sheetId": sid, "startRowIndex": 7, "endRowIndex": 500,
        "startColumnIndex": 14, "endColumnIndex": 15}}})
    for old_col, options in VALUES.items():
        col = DISPLAY_ORDER.index(old_col)
        requests.append({"setDataValidation": {
            "range": {"sheetId": sid, "startRowIndex": 7, "endRowIndex": 500,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                       "values": [{"userEnteredValue": v} for v in options]},
                     "strict": True, "showCustomUi": True}}})
    for i, row in enumerate(rows[7:], start=7):
        if not row[1].strip():
            requests.append({"setDataValidation": {"range": {
                "sheetId": sid, "startRowIndex": i, "endRowIndex": i + 1,
                "startColumnIndex": 8, "endColumnIndex": 13}}})
    raid_sheets._json(
        session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
        json={"requests": requests},
    )

def _write(session, title, sid, existing, rows):
    width = len(COLUMNS)
    current = [(row + [""] * width)[:width] for row in existing]
    needed = rows + [[""] * width] * max(0, len(current) - len(rows))
    if len(needed) > 500:
        raise RuntimeError("Raid tab has too many populated rows to rewrite safely.")
    old_display = [_display(r) for r in current]
    new_display = [_display(r) for r in needed]
    changed = old_display != new_display
    if changed:
        raid_sheets._json(
            session, "PUT", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/"
            + quote(f"'{title}'!A1:S{len(needed)}", safe="'!:"),
            params={"valueInputOption": "RAW"},
            json={"values": new_display},
        )
    # Even when data is unchanged, repair pre-existing fixed-position styling
    # once on the next sync following deployment/restart.
    layout = _section_positions(rows)
    if changed or _FORMATTED_SECTION_LAYOUTS.get(sid) != layout:
        _paint_sections(session, sid, rows)
        _FORMATTED_SECTION_LAYOUTS[sid] = layout
    return sum(a != b for a, b in zip(
        old_display + [[""] * width] * max(0, len(needed) - len(current)),
        new_display))


def _one(session, db_path, event, names, titles):
    number = event["raid_id"]
    matches = [name for name in titles if name.startswith(f"Raid #{number} - ")]
    title = tab_title(event)
    new = not matches
    if new:
        sid = _make_tab(session, title)
        titles[title] = {"sheetId": sid, "title": title}
    else:
        original = matches[0]
        sid = titles[original]["sheetId"]
        if original != title and not event.get("archived_at"):
            raid_sheets._json(
                session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
                json={"requests": [{"updateSheetProperties": {
                    "properties": {"sheetId": sid, "title": title},
                    "fields": "title"}}]},
            )
            titles[title] = titles.pop(original)
            titles[title]["title"] = title
    if not new:
        count = titles[title].get("gridProperties", {}).get("columnCount", len(COLUMNS))
        if count == 18:
            # Migrate the old R Signed At into S without losing any timestamps.
            # Appending alone would wrongly read the old signed timestamp as Absent At.
            raid_sheets._json(
                session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
                json={"requests": [
                    {"appendDimension": {
                        "sheetId": sid, "dimension": "COLUMNS", "length": 1}},
                    {"moveDimension": {
                        "source": {"sheetId": sid, "dimension": "COLUMNS",
                                   "startIndex": 17, "endIndex": 18},
                        "destinationIndex": 19}},
                    {"updateDimensionProperties": {
                        "range": {"sheetId": sid, "dimension": "COLUMNS",
                                  "startIndex": 17, "endIndex": 19},
                        "properties": {"pixelSize": 195}, "fields": "pixelSize"}},
                    {"repeatCell": {
                        "range": {"sheetId": sid, "startRowIndex": 6,
                                  "endRowIndex": 7, "startColumnIndex": 17,
                                  "endColumnIndex": 19},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                            "textFormat": {"fontFamily": "Roboto",
                                           "fontSize": 12, "bold": True}}},
                        "fields": "userEnteredFormat"}},
                    {"repeatCell": {
                        "range": {"sheetId": sid, "startRowIndex": 7,
                                  "endRowIndex": 500, "startColumnIndex": 17,
                                  "endColumnIndex": 19},
                        "cell": {"userEnteredFormat": {
                            "textFormat": {"fontFamily": "Roboto", "fontSize": 10},
                            "verticalAlignment": "MIDDLE",
                            "horizontalAlignment": "CENTER"}},
                        "fields": "userEnteredFormat.textFormat,"
                                  "userEnteredFormat.verticalAlignment,"
                                  "userEnteredFormat.horizontalAlignment"}},
                ]},
            )
        elif count < len(COLUMNS):
            raise RuntimeError("Raid tab has an unexpected number of columns.")
    existing = [] if new else _read(session, title)
    # Also recover a sheet created just before an interrupted first write.
    legacy = _legacy_choices(session, event) if new or not existing else None
    body = _rows(db_path, event, names, old_rows=existing, legacy=legacy)
    full = _header(event, body, title)
    changed = _write(session, title, sid, existing, full)
    if event.get("archived_at"):
        if not titles[title].get("hidden"):
            raid_sheets._json(
                session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
                json={"requests": [{"updateSheetProperties": {
                    "properties": {"sheetId": sid, "hidden": True},
                    "fields": "hidden"}}]},
            )
    return changed, full


def read_selected(db_path, raid_id):
    """Live selection snapshot for /raid confirm; don't trust stale cache."""
    event = read_raid(db_path, raid_id)
    if not event:
        raise ValueError("Raid not found.")
    if not raid_sheets.configured():
        raise RuntimeError("Google Sheets is not connected.")
    session = raid_sheets._session()
    try:
        titles = _list_tabs(session)
        title = tab_title(event)
        if title not in titles:
            raise ValueError("Raid tab not created yet. Run /raid sync first.")
        rows = _read(session, title)
        absent = {str(item["discord_id"]) for item in
                  raid_sheets._raid_snapshot(db_path)[3]
                  if item["raid_id"] == raid_id}
        return [entry for entry in _approved(rows, event, include_details=True)
                if not entry[0] or entry[0] not in absent]
    finally:
        session.close()


def _hide_legacy(session, titles):
    outdated = [details["sheetId"] for name, details in titles.items()
                if name.startswith("RM - ") and not details.get("hidden")]
    if not outdated:
        return
    raid_sheets._json(
        session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
        json={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": sid, "hidden": True}, "fields": "hidden",
        }} for sid in outdated]},
    )
    for name, details in titles.items():
        if name.startswith("RM - "):
            details["hidden"] = True



def _permanent_read(session, tab):
    url = f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/" + quote(
        f"'{tab}'!A5:O{500 if tab == 'Forever Roster' else 1000}", safe="'!:")
    return raid_sheets._json(session, "GET", url).get("values", [])


def _permanent_write(session, tab, row_number, values):
    url = f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/" + quote(
        f"'{tab}'!A{row_number}:O{row_number}", safe="'!:")
    raid_sheets._json(session, "PUT", url, params={"valueInputOption": "RAW"},
                      json={"values": [values]})


def _attendance_formula(row_number, status):
    return (f'=SUMPRODUCT((\'Forever Attendance\'!$C$6:$C$1000=$B{row_number})*'
            f'(IFERROR(DATEVALUE(\'Forever Attendance\'!$B$6:$B$1000),0)>=TODAY()-90)*'
            f'(\'Forever Attendance\'!$J$6:$J$1000="{status}"))')


def _sync_permanent(session, db_path, discord_names, raid_rosters):
    """Mirror characters and selected raid members; never overwrite manual attendance."""
    characters, raids, _, _, gear = raid_sheets._raid_snapshot(db_path)
    discord_names = discord_names or {}
    gear_by_id = {str(g["character_id"]): g for g in gear}
    old_roster = _permanent_read(session, "Forever Roster")
    old_attendance = _permanent_read(session, "Forever Attendance")
    roster_map = {str(row[0]): (i + 6, row) for i, row in enumerate(old_roster[1:])
                  if row and str(row[0]).isdigit()}
    attendance_map = {(str(row[0]), str(row[2])): (i + 6, row)
                      for i, row in enumerate(old_attendance[1:])
                      if len(row) > 2 and str(row[0]).isdigit() and str(row[2]).isdigit()}
    roster_next = len(old_roster) + 5
    attendance_next = len(old_attendance) + 5
    for character in characters:
        cid = str(character["character_id"])
        did = str(character["discord_id"])
        previous = roster_map.get(cid)
        row_number = previous[0] if previous else roster_next
        if not previous:
            roster_next += 1
        old = previous[1] if previous else []
        name = discord_names.get(int(did), "") or (old[2] if len(old) > 2 else "")
        gear = gear_by_id.get(cid, {})
        gear_date = max(gear.get("simc_latest") or "", gear.get("image_latest") or "")
        counts = [
            _attendance_formula(row_number, "Attended"),
            _attendance_formula(row_number, "Absent"),
            _attendance_formula(row_number, "Excused"),
        ]
        ratio = (f'=IFERROR(L{row_number}/(L{row_number}+M{row_number}),"—")')
        values = [cid, did, name, character["name"], character["class_name"],
                  character["spec"], character["role"],
                  "Main" if character["is_main"] else "Alt",
                  "", gear_date, ratio, *counts,
                  old[14] if len(old) > 14 else ""]
        # Formulas recalculate in Sheets; avoid rewriting unchanged profiles.
        if not previous or old[:10] != [str(v) for v in values[:10]]:
            _permanent_write(session, "Forever Roster", row_number,
                             values[:10] + [""] * 4 + values[14:])
            url = f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/" + quote(
                f"'Forever Roster'!K{row_number}:N{row_number}", safe="'!:")
            raid_sheets._json(session, "PUT", url,
                params={"valueInputOption": "USER_ENTERED"},
                json={"values": [[ratio, *counts]]})
    for raid_id, players in raid_rosters.items():
        event = raids.get(raid_id)
        if not event or not (event.get("roster_published") or event.get("archived_at")):
            continue
        date = datetime.fromisoformat(event["raid_at"]).astimezone(UK).strftime("%Y-%m-%d")
        for row in players:
            did, cid = str(row[16]), str(row[17])
            if not did.isdigit():
                continue
            key = (str(raid_id), did)
            old_entry = attendance_map.get(key)
            if old_entry:
                # Attendance, late/AFK, excused and notes belong to leadership.
                continue
            values = [str(raid_id), date, did, row[0], cid, row[1],
                      row[2], row[4], row[9], "Pending", "", "", "No", "", ""]
            _permanent_write(session, "Forever Attendance", attendance_next, values)
            attendance_map[key] = (attendance_next, values)
            attendance_next += 1

def sync_once(db_path, discord_names=None):
    """Synchronise only posted raids into one private tab each."""
    if not raid_sheets.configured():
        raise RuntimeError("Google Sheets credentials are not configured.")
    session = raid_sheets._session()
    try:
        _, raids, _, absences, _ = raid_sheets._raid_snapshot(db_path)
        titles = _list_tabs(session)
        counts, published = {"raid_tabs": 0}, {}
        raid_rosters = {}
        unavailable = {}
        for absence in absences:
            unavailable.setdefault(absence["raid_id"], set()).add(
                str(absence["discord_id"]))
        for raid_id, event in raids.items():
            # Draft raids are not posted and must not clutter the Sheet.
            if not event.get("panel_message_id"):
                continue
            if event.get("archived_at") and not any(
                    title.startswith(f"Raid #{raid_id} - ") and
                    not props.get("hidden") for title, props in titles.items()):
                continue
            changes, rows = _one(session, db_path, event, discord_names, titles)
            counts["raid_tabs"] += changes
            raid_rosters[raid_id] = [r for r in rows[7:]
                if len(r) >= 18 and r[16].isdigit() and r[9] in ("Selected", "Bench")
                and r[8] == "Signed"]
            if event.get("roster_published"):
                published[raid_id] = confirmed_roster(
                    db_path, raid_id, unavailable.get(raid_id, ()),
                    include_details=True
                )
            else:
                # The collecting roster is sourced only from the actual raid
                # sign-ups. Sheet selection, Show on Discord and background
                # raid assignments remain private until leadership confirms.
                published[raid_id] = read_signup_roster(db_path, raid_id)
        _sync_permanent(session, db_path, discord_names, raid_rosters)
        # Archived raids retain their most recent public roster and history.
        # Once an active raid tab exists, hide (never delete) old management tabs.
        if any(name.startswith("Raid #") for name in titles):
            _hide_legacy(session, titles)
        changed = set()
        for raid_id, selected in published.items():
            changed.update(_save_public_for_one(db_path, raid_id, selected))
        return counts, changed
    finally:
        session.close()


def attendance_candidates(db_path, raid_id):
    """Confirmed raid's selected and bench sign-ups, keyed by Discord ID."""
    event = read_raid(db_path, raid_id)
    if not event or not event.get("roster_published"):
        raise ValueError("Confirm the roster before recording attendance.")
    session = raid_sheets._session()
    try:
        rows = _read(session, tab_title(event))
        return [row for row in rows[7:] if len(row) >= 18
                and row[16].isdigit() and row[8] == "Signed"
                and row[9] in ("Selected", "Bench")]
    finally:
        session.close()


def attendance_set(db_path, raid_id, member_id, outcome, minutes=0):
    """Upsert one raid attendance result without duplicating or clearing notes."""
    if outcome not in ("Attended", "Absent", "Excused", "Pending"):
        raise ValueError("Invalid attendance outcome.")
    candidates = attendance_candidates(db_path, raid_id)
    member = next((r for r in candidates if r[16] == str(member_id)), None)
    if not member:
        raise ValueError("Player is not selected or benched for this raid.")
    session = raid_sheets._session()
    try:
        existing = _permanent_read(session, "Forever Attendance")
        matched = next(((i + 6, row) for i, row in enumerate(existing[1:])
                        if len(row) > 2 and row[0] == str(raid_id)
                        and row[2] == str(member_id)), None)
        row_number = matched[0] if matched else len(existing) + 5
        old = matched[1] if matched else []
        event = read_raid(db_path, raid_id)
        date = datetime.fromisoformat(event["raid_at"]).astimezone(UK).strftime("%Y-%m-%d")
        now = datetime.now(UK).isoformat(timespec="seconds")
        values = [str(raid_id), date, str(member_id), member[0], member[17],
                  member[1], member[2], member[4], member[9], outcome,
                  str(minutes) if outcome == "Attended" and minutes else
                  (old[10] if len(old) > 10 and outcome == "Attended" else ""),
                  old[11] if len(old) > 11 else "",
                  "Yes" if outcome == "Excused" else "No",
                  old[13] if len(old) > 13 else "", now]
        _permanent_write(session, "Forever Attendance", row_number, values)
        return member[1]
    finally:
        session.close()


def attendance_complete(db_path, raid_id):
    """Fill only Pending/empty records; preserve officer-entered exceptions."""
    candidates = attendance_candidates(db_path, raid_id)
    session = raid_sheets._session()
    try:
        existing = _permanent_read(session, "Forever Attendance")
        keyed = {(r[0], r[2]): r for r in existing[1:]
                 if len(r) > 2}
    finally:
        session.close()
    for member in candidates:
        old = keyed.get((str(raid_id), member[16]), [])
        if len(old) <= 9 or old[9] in ("", "Pending"):
            attendance_set(db_path, raid_id, member[16], "Attended")
    return len(candidates)


def _save_public_for_one(db_path, raid_id, selected):
    """Retain class, role and spec for Discord emoji rendering."""
    desired = [
        tuple(row[:5]) + ("",) * (5 - len(row[:5]))
        for row in selected
    ]
    with database(db_path) as con:
        current = con.execute(
            "SELECT name, assignment, COALESCE(class_name,''), COALESCE(role,''), "
            "COALESCE(spec,'') FROM raid_public_roster "
            "WHERE raid_id=? ORDER BY position", (raid_id,)
        ).fetchall()
        if current == desired:
            return set()
        from datetime import datetime, timezone
        instant = datetime.now(timezone.utc).isoformat()
        con.execute(
            "UPDATE raids SET roster_updated_at=CASE "
            "WHEN roster_published=1 THEN ? ELSE roster_updated_at END "
            "WHERE raid_id=?", (instant, raid_id),
        )
        con.execute("DELETE FROM raid_public_roster WHERE raid_id=?", (raid_id,))
        con.executemany(
            "INSERT INTO raid_public_roster "
            "(raid_id, position, name, assignment, class_name, role, spec) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(raid_id, i, *entry) for i, entry in enumerate(desired)],
        )
    return {raid_id}
