"""High-level dungeon farming requests for the Hyped LFG board.

Requests live in the existing LFG SQLite file. A character_id always refers to
the canonical shared raid_signup.characters table, not another character copy.
Do not modify the original launch-plan rows during migration.
"""
import re
from urllib.parse import urlsplit

# Includes Classic dungeons whose level range overlaps 45+ and the three
# new level-48+ Forever dungeons. All options fit one Discord multiselect.
DUNGEONS = (
    "Uldaman", "Zul'Farrak", "Maraudon", "Alcaz Prison",
    "Sunken Temple", "Blackrock Depths", "Dire Maul East",
    "Blackmaw Hold", "Lower Blackrock Spire", "Dire Maul North",
    "Dire Maul West", "Scholomance", "Stratholme Live",
    "Stratholme Undead", "Shaper's Terrace", "Upper Blackrock Spire",
)
DUNGEON_SET = frozenset(DUNGEONS)
MAX_ITEMS = 5


def validate_dungeons(values):
    """Validate a nonempty, deduplicated selection in stable level order."""
    selected = set(values)
    if not selected or selected - DUNGEON_SET:
        raise ValueError("Choose at least one listed level 45+ dungeon.")
    return tuple(d for d in DUNGEONS if d in selected)


def parse_item_ids(value):
    """Accept bare numeric item IDs or full Wowhead Forever item links.

    Never include user-supplied URLs in a public Discord message.
    """
    tokens = [v.strip() for v in re.split(r"[,;\n]+", value or "") if v.strip()]
    if len(tokens) > MAX_ITEMS:
        raise ValueError("Add up to five item IDs.")
    result = []
    for token in tokens:
        if token.isdecimal():
            raw_id = token
        else:
            parsed = urlsplit(token)
            if (parsed.scheme != "https"
                    or parsed.hostname not in ("www.wowhead.com", "wowhead.com")
                    or parsed.username or parsed.password or parsed.port
                    or parsed.fragment):
                raise ValueError("Use a numeric Wowhead item ID or a Forever item link.")
            match = re.fullmatch(r"/forever/item=(\d+)/?", parsed.path)
            if not match or parsed.query:
                raise ValueError("Use a Wowhead Forever item link, not another URL.")
            raw_id = match.group(1)
        item_id = int(raw_id)
        if not 1 <= item_id <= 100_000_000:
            raise ValueError("Item IDs must be positive and valid-looking.")
        if item_id not in result:
            result.append(item_id)
    return tuple(result)


def item_links(items):
    """Only safe canonical Wowhead links, labelled by verified numeric ID."""
    return ", ".join(
        f"[Item #{item}](https://www.wowhead.com/forever/item={item})"
        for item in items
    )


def init_requests(db):
    db.execute("""CREATE TABLE IF NOT EXISTS lfg_dungeon_requests (
        channel_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        dungeons TEXT NOT NULL,
        item_ids TEXT NOT NULL DEFAULT '',
        availability TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        avatar_url TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(channel_id, user_id)
    )""")


def get_request(db, channel_id, user_id):
    row = db.execute(
        "SELECT * FROM lfg_dungeon_requests WHERE channel_id=? AND user_id=?",
        (channel_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def list_requests(db, channel_id):
    return [
        dict(row) for row in db.execute(
            "SELECT * FROM lfg_dungeon_requests WHERE channel_id=? "
            "ORDER BY updated_at DESC, user_id",
            (channel_id,),
        ).fetchall()
    ]


def save_request(db, channel_id, user_id, character_id, dungeons,
                 item_ids, availability, notes="", avatar_url=""):
    chosen = validate_dungeons(dungeons)
    items = parse_item_ids(item_ids) if isinstance(item_ids, str) else tuple(item_ids)
    if len(items) > MAX_ITEMS or any(
        not isinstance(item, int) or not 1 <= item <= 100_000_000
        for item in items
    ):
        raise ValueError("Invalid item ID selection.")
    availability = " ".join((availability or "").split())
    notes = " ".join((notes or "").split())
    if not 1 <= len(availability) <= 120:
        raise ValueError("Enter your availability (up to 120 characters).")
    if len(notes) > 180:
        raise ValueError("Notes must be 180 characters or less.")
    db.execute(
        """INSERT INTO lfg_dungeon_requests
            (channel_id, user_id, character_id, dungeons, item_ids,
             availability, notes, avatar_url)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(channel_id,user_id) DO UPDATE SET
             character_id=excluded.character_id,
             dungeons=excluded.dungeons,
             item_ids=excluded.item_ids,
             availability=excluded.availability,
             notes=excluded.notes,
             avatar_url=excluded.avatar_url,
             updated_at=CURRENT_TIMESTAMP""",
        (channel_id, user_id, character_id, ", ".join(chosen),
         ",".join(str(i) for i in items), availability, notes, avatar_url),
    )


def delete_request(db, channel_id, user_id):
    return db.execute(
        "DELETE FROM lfg_dungeon_requests WHERE channel_id=? AND user_id=?",
        (channel_id, user_id),
    ).rowcount
