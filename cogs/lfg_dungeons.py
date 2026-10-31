"""High-level dungeon farming requests for the Hyped LFG board.

Requests live in the existing LFG SQLite file. A character_id always refers to
the canonical shared raid_signup.characters table, not another character copy.
Do not modify the original launch-plan rows during migration.
"""

# The Wowhead Forever dungeon overview combines all levelling and high-level
# Classic dungeons with the nine new Forever dungeons:
# https://www.wowhead.com/forever/guide/dungeons-overview-locations-details
#
# Keep existing stored dungeon names unchanged. Order the 35 options by their
# starting level, not by category. Discord permits only 25 choices per select,
# so the private picker renders two dropdowns in one screen, not category pages.
DUNGEONS = (
    "Ragefire Chasm", "Hall of Thanes", "Ruins of Lordaeron",
    "Wailing Caverns", "The Deadmines", "Shadowfang Keep",
    "The Stockade", "Blackfathom Deeps", "Excavation Site",
    "Scarlet Monastery Graveyard", "City of Dalaran",
    "Gnomeregan", "Scarlet Monastery Library", "Razorfen Kraul",
    "Scarlet Monastery Armory", "The Drowned City",
    "Scarlet Monastery Cathedral", "Razorfen Downs",
    "Krol'dok Stronghold", "Uldaman", "Zul'Farrak", "Maraudon",
    "Alcaz Prison", "Sunken Temple", "Blackrock Depths",
    "Lower Blackrock Spire", "Blackmaw Hold", "Dire Maul East",
    "Dire Maul North", "Dire Maul West", "Upper Blackrock Spire",
    "Scholomance", "Stratholme Live", "Stratholme Undead",
    "Shaper's Terrace",
)
DUNGEON_SET = frozenset(DUNGEONS)
DUNGEON_SELECT_SIZE = 18
DUNGEON_SELECT_GROUPS = tuple(
    DUNGEONS[offset:offset + DUNGEON_SELECT_SIZE]
    for offset in range(0, len(DUNGEONS), DUNGEON_SELECT_SIZE)
)
# Approximate dungeon level ranges from the linked Wowhead overview.
# Existing variants/wings use their parent dungeon's suggested range.
DUNGEON_LEVELS = {
    "Ragefire Chasm": "13–18", "Hall of Thanes": "13–18",
    "Ruins of Lordaeron": "15–20", "Wailing Caverns": "15–25",
    "The Deadmines": "18–23", "Shadowfang Keep": "22–30",
    "The Stockade": "22–30", "Blackfathom Deeps": "24–32",
    "Excavation Site": "24–29", "Scarlet Monastery Graveyard": "28–38",
    "City of Dalaran": "28–33", "Gnomeregan": "29–38",
    "Scarlet Monastery Library": "29–39", "Razorfen Kraul": "30–40",
    "Scarlet Monastery Armory": "32–42", "The Drowned City": "35–40",
    "Scarlet Monastery Cathedral": "35–45", "Razorfen Downs": "40–50",
    "Krol'dok Stronghold": "40–45", "Uldaman": "42–52",
    "Zul'Farrak": "44–54", "Maraudon": "45–57",
    "Alcaz Prison": "48–53", "Sunken Temple": "50–60",
    "Blackrock Depths": "52–60", "Lower Blackrock Spire": "55–60",
    "Blackmaw Hold": "55–60", "Dire Maul East": "58–60",
    "Dire Maul North": "58–60", "Dire Maul West": "58–60",
    "Upper Blackrock Spire": "58–60", "Scholomance": "58–60",
    "Stratholme Live": "58–60", "Stratholme Undead": "58–60",
    "Shaper's Terrace": "58–60",
}
MAX_ITEMS_TEXT = 250


def validate_dungeons(values):
    """Validate a nonempty, deduplicated selection in stable level order."""
    selected = set(values)
    if not selected or selected - DUNGEON_SET:
        raise ValueError("Choose at least one listed dungeon.")
    return tuple(dungeon for dungeon in DUNGEONS if dungeon in selected)


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
