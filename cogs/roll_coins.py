"""One durable gold coin per completed Hyped roll day.

The legacy rolls table belongs entirely to Hyped. Derive dates from timestamps
in Europe/London, including older rows whose roll_date was populated in UTC.
This module never changes existing rolls, scores, crowns or reset records.
"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")


def ensure_coin_schema(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS roll_coin_awards (
            guild_id INTEGER NOT NULL,
            roll_date TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            winning_roll INTEGER NOT NULL CHECK(winning_roll BETWEEN 1 AND 100),
            source TEXT NOT NULL CHECK(source IN ('rolls', 'manual')),
            awarded_at INTEGER NOT NULL,
            awarded_by INTEGER,
            PRIMARY KEY(guild_id, roll_date)
        )
    """)
    con.execute("""CREATE INDEX IF NOT EXISTS idx_coin_awards_user
                   ON roll_coin_awards(guild_id, user_id)""")


def settle_completed_days(con, guild_id, now=None):
    """Backfill missing awards only. Safe to rerun after restarts or failures."""
    if not guild_id:
        return 0
    now = now or datetime.now(UK)
    today = now.astimezone(UK).date()
    cutoff = int(datetime.combine(today, time.min, UK).timestamp())
    # ORDER BY supplies the existing crown tie rule: earliest highest roll.
    # rowid breaks ties between rolls recorded in the very same second.
    rows = con.execute("""
        SELECT user_id, username, value, rolled_at FROM rolls
        WHERE actor_type='user' AND rolled_at < ? AND value BETWEEN 1 AND 100
        ORDER BY value DESC, rolled_at ASC, rowid ASC
    """, (cutoff,))
    winners = {}
    for user_id, username, value, rolled_at in rows:
        day = datetime.fromtimestamp(rolled_at, UK).date().isoformat()
        winners.setdefault(day, (user_id, username, value))
    added = 0
    con.execute("SAVEPOINT settle_coins")
    try:
        for day, (user_id, username, value) in sorted(winners.items()):
            result = con.execute("""
                INSERT OR IGNORE INTO roll_coin_awards
                (guild_id, roll_date, user_id, username, winning_roll, source, awarded_at)
                VALUES (?, ?, ?, ?, ?, 'rolls', ?)
            """, (guild_id, day, user_id, username, value, int(now.timestamp())))
            added += result.rowcount
        con.execute("RELEASE settle_coins")
    except Exception:
        con.execute("ROLLBACK TO settle_coins")
        con.execute("RELEASE settle_coins")
        raise
    return added


def coin_total(con, guild_id, user_id):
    return con.execute("""SELECT COUNT(*) FROM roll_coin_awards
                          WHERE guild_id=? AND user_id=?""",
                       (guild_id, user_id)).fetchone()[0]


def day_award(con, guild_id, day):
    row = con.execute("""SELECT user_id, username, winning_roll, source
                         FROM roll_coin_awards WHERE guild_id=? AND roll_date=?""",
                      (guild_id, day)).fetchone()
    if row is None:
        return None
    return dict(zip(("user_id", "username", "winning_roll", "source"), row))


def coin_standings(con, guild_id):
    rows = con.execute("""
        SELECT user_id, COUNT(*) AS coins FROM roll_coin_awards
        WHERE guild_id=? GROUP BY user_id ORDER BY coins DESC, user_id ASC
    """, (guild_id,)).fetchall()
    ranked = []
    previous, rank = None, 0
    for position, (user_id, coins) in enumerate(rows, 1):
        if coins != previous:
            rank = position
        ranked.append({"rank": rank, "user_id": user_id, "coins": coins})
        previous = coins
    return ranked


def award_missing_day(con, guild_id, day, user_id, username, value, owner_id, now=None):
    """Owner recovery for a historical day missing from the database.

    Existing automatic or manual awards cannot be overwritten or duplicated.
    """
    now = now or datetime.now(UK)
    parsed = date.fromisoformat(day)
    if parsed >= now.astimezone(UK).date():
        raise ValueError("Choose a completed day, before today (UK time).")
    if not 1 <= value <= 100:
        raise ValueError("The winning roll must be between 1 and 100.")
    # Saved rolls take priority over a manual recovery for the same day.
    settle_completed_days(con, guild_id, now)
    result = con.execute("""
        INSERT OR IGNORE INTO roll_coin_awards
        (guild_id, roll_date, user_id, username, winning_roll, source, awarded_at, awarded_by)
        VALUES (?, ?, ?, ?, ?, 'manual', ?, ?)
    """, (guild_id, parsed.isoformat(), user_id, username, value,
          int(now.timestamp()), owner_id))
    return result.rowcount == 1
