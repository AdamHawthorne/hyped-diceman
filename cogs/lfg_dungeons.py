"""High-level dungeon farming requests for the Hyped LFG board.

Requests live in the existing LFG SQLite file. A character_id always refers to
the canonical shared raid_signup.characters table, not another character copy.
Do not modify the original launch-plan rows during migration.
"""

# Discord limits a select to 25 options, so allow players to make a
# cumulative selection across these four categories. Existing saved dungeon
# names are retained exactly to avoid changing existing requests.
# New Forever dungeon names follow Blizzard's 20 October 2026 overview.
ACTIVITY_GROUPS = {
    "Classic: Levelling": (
        "Ragefire Chasm", "Wailing Caverns", "The Deadmines",
        "Shadowfang Keep", "Blackfathom Deeps", "The Stockade",
        "Razorfen Kraul", "Gnomeregan", "Scarlet Monastery Graveyard",
        "Scarlet Monastery Library", "Scarlet Monastery Armory",
        "Scarlet Monastery Cathedral", "Razorfen Downs", "Uldaman",
    ),
    "Classic: High-level": (
        "Zul'Farrak", "Maraudon", "Sunken Temple",
        "Blackrock Depths", "Dire Maul East", "Dire Maul West",
        "Dire Maul North", "Lower Blackrock Spire",
        "Upper Blackrock Spire", "Scholomance",
        "Stratholme Live", "Stratholme Undead",
    ),
    "Forever": (
        "Hall of Thanes", "Ruins of Lordaeron", "Excavation Site",
        "City of Dalaran", "The Drowned City", "Krol'dok Stronghold",
        "Alcaz Prison", "Blackmaw Hold", "Shaper's Terrace",
    ),
    "PvP": (
        "Warsong Gulch", "Arathi Basin", "Alterac Valley",
        "World PvP", "Battleground Premades",
    ),
}
DUNGEONS = tuple(
    name for category, options in ACTIVITY_GROUPS.items()
    if category != "PvP" for name in options
)
ACTIVITIES = tuple(
    name for options in ACTIVITY_GROUPS.values() for name in options
)
DUNGEON_SET = frozenset(ACTIVITIES)
MAX_ITEMS_TEXT = 250


def validate_dungeons(values):
    """Validate a nonempty, deduplicated selection in stable level order."""
    selected = set(values)
    if not selected or selected - DUNGEON_SET:
        raise ValueError("Choose at least one listed dungeon or PvP activity.")
    return tuple(activity for activity in ACTIVITIES if activity in selected)


def normalise_wanted_items(value):
    """Free-text wishlist. Never resolve or render an untrusted item URL."""
    items = " ".join((value or "").split())
    if len(items) > MAX_ITEMS_TEXT:
        raise ValueError("Item wishlist must be 250 characters or less.")
    return items


def init_requests(db):
    db.execute("""CREATE TABLE IF NOT EXISTS lfg_dungeon_requests (
        channel_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        dungeons TEXT NOT NULL,
        item_ids TEXT NOT NULL DEFAULT '',
        wanted_items TEXT NOT NULL DEFAULT '',
        availability TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        avatar_url TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(channel_id, user_id)
    )""")
    columns = {row[1] for row in db.execute(
        "PRAGMA table_info(lfg_dungeon_requests)")}
    if "wanted_items" not in columns:
        db.execute("ALTER TABLE lfg_dungeon_requests "
                   "ADD COLUMN wanted_items TEXT NOT NULL DEFAULT ''")
        # Preserve any existing item IDs as ordinary text. No lookup or links.
        db.execute("UPDATE lfg_dungeon_requests "
                   "SET wanted_items=REPLACE(item_ids, ',', ', ') "
                   "WHERE item_ids!='' AND wanted_items=''")
        

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
                 wanted_items, availability, notes="", avatar_url=""):
    chosen = validate_dungeons(dungeons)
    items = normalise_wanted_items(wanted_items)
    availability = " ".join((availability or "").split())
    notes = " ".join((notes or "").split())
    if not 1 <= len(availability) <= 120:
        raise ValueError("Enter your availability (up to 120 characters).")
    if len(notes) > 180:
        raise ValueError("Notes must be 180 characters or less.")
    db.execute(
        """INSERT INTO lfg_dungeon_requests
            (channel_id, user_id, character_id, dungeons, wanted_items, item_ids,
             availability, notes, avatar_url)
           VALUES (?,?,?,?,?,'',?,?,?)
           ON CONFLICT(channel_id,user_id) DO UPDATE SET
             character_id=excluded.character_id,
             dungeons=excluded.dungeons,
             wanted_items=excluded.wanted_items,
             item_ids='',
             availability=excluded.availability,
             notes=excluded.notes,
             avatar_url=excluded.avatar_url,
             updated_at=CURRENT_TIMESTAMP""",
        (channel_id, user_id, character_id, ", ".join(chosen),
         items, availability, notes, avatar_url),
    )


def delete_request(db, channel_id, user_id):
    return db.execute(
        "DELETE FROM lfg_dungeon_requests WHERE channel_id=? AND user_id=?",
        (channel_id, user_id),
    ).rowcount
