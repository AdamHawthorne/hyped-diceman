"""One durable gold coin per completed Hyped roll day.

The legacy rolls table belongs entirely to Hyped. Derive dates from timestamps
in Europe/London, including older rows whose roll_date was populated in UTC.
This module never changes existing rolls, scores, crowns or reset records.
"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
# Do not retroactively double previous Sunday prizes when migrating existing data.
SUNDAY_DOUBLE_START = date(2026, 11, 1)


def daily_coin_prize(roll_day):
    """One winner per day. Sundays on/after launch award two coins."""
    roll_day = date.fromisoformat(roll_day) if isinstance(roll_day, str) else roll_day
    return 2 if roll_day >= SUNDAY_DOUBLE_START and roll_day.weekday() == 6 else 1



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
    # Keep all historical awards at one coin. Only future Sunday winners
    # receive two; never rewrite old ledger rows in a migration.
    award_columns = {row[1] for row in con.execute("PRAGMA table_info(roll_coin_awards)")}
    if "coins_awarded" not in award_columns:
        con.execute(
            "ALTER TABLE roll_coin_awards ADD COLUMN "
            "coins_awarded INTEGER NOT NULL DEFAULT 1 CHECK(coins_awarded IN (1, 2))"
        )
    con.execute("""CREATE INDEX IF NOT EXISTS idx_coin_awards_user
                   ON roll_coin_awards(guild_id, user_id)""")
    # Atomic, once-a-day gamble outcomes. A returned stake never generates
    # another coin; a lost stake subtracts from the currently held balance.
    con.execute("""CREATE TABLE IF NOT EXISTS roll_coin_gambles (
        guild_id INTEGER NOT NULL,
        roll_date TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        base_roll INTEGER NOT NULL CHECK(base_roll BETWEEN 1 AND 100),
        gamble_roll INTEGER NOT NULL CHECK(gamble_roll BETWEEN 1 AND 100),
        stake_returned INTEGER NOT NULL CHECK(stake_returned IN (0, 1)),
        played_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id, roll_date, user_id)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_roll_coin_gambles_user
                   ON roll_coin_gambles(guild_id, user_id)""")
    con.execute("""CREATE TABLE IF NOT EXISTS roll_sunday_bonus_notices (
        guild_id INTEGER NOT NULL,
        roll_date TEXT NOT NULL,
        message_id INTEGER NOT NULL,
        PRIMARY KEY(guild_id, roll_date)
    )""")
    # Durable UK-day gate shared by the leaderboard button and /leaderboard.
    # A single successful INSERT reserves the day's one public display.
    con.execute("""
        CREATE TABLE IF NOT EXISTS roll_public_leaderboards (
            guild_id INTEGER NOT NULL,
            roll_date TEXT NOT NULL,
            requested_by INTEGER NOT NULL,
            claimed_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id, roll_date)
        )
    """)




def claim_public_coin_leaderboard(con, guild_id, roll_date, user_id, now=None):
    """Reserve at most one public Ledger display per guild per UK calendar day.

    SQLite's unique key also serialises near-simultaneous button and slash
    requests. Other requests can still receive a private ephemeral copy.
    The reservation survives bot restarts on the existing persistent DB.
    """
    now = now or datetime.now(UK)
    result = con.execute("""
        INSERT OR IGNORE INTO roll_public_leaderboards
            (guild_id, roll_date, requested_by, claimed_at)
        VALUES (?, ?, ?, ?)
    """, (guild_id, roll_date, user_id, int(now.timestamp())))
    return result.rowcount == 1


def release_public_coin_leaderboard(con, guild_id, roll_date, user_id):
    """Release a failed reservation only if the same requester claimed it."""
    return con.execute("""
        DELETE FROM roll_public_leaderboards
        WHERE guild_id=? AND roll_date=? AND requested_by=?
    """, (guild_id, roll_date, user_id)).rowcount == 1

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
        WHERE actor_type IN ('user', 'gamble', 'bot') AND rolled_at < ? AND value BETWEEN 1 AND 100
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
                (guild_id, roll_date, user_id, username, winning_roll, source, awarded_at,
                 coins_awarded)
                VALUES (?, ?, ?, ?, ?, 'rolls', ?, ?)
            """, (guild_id, day, user_id, username, value, int(now.timestamp()),
                  daily_coin_prize(day)))
            added += result.rowcount
        con.execute("RELEASE settle_coins")
    except Exception:
        con.execute("ROLLBACK TO settle_coins")
        con.execute("RELEASE settle_coins")
        raise
    return added


def coin_total(con, guild_id, user_id):
    """Spendable coin balance, never destructive edits to earned award rows."""
    earned = con.execute("""
        SELECT COALESCE(SUM(coins_awarded), 0)
        FROM roll_coin_awards WHERE guild_id=? AND user_id=?
    """, (guild_id, user_id)).fetchone()[0]
    lost = con.execute("""
        SELECT COUNT(*) FROM roll_coin_gambles
        WHERE guild_id=? AND user_id=? AND stake_returned=0
    """, (guild_id, user_id)).fetchone()[0]
    return earned - lost


def day_award(con, guild_id, day):
    row = con.execute("""
        SELECT user_id, username, winning_roll, source, coins_awarded
        FROM roll_coin_awards WHERE guild_id=? AND roll_date=?
    """, (guild_id, day)).fetchone()
    if row is None:
        return None
    return dict(zip(
        ("user_id", "username", "winning_roll", "source", "coins_awarded"), row
    ))


def coin_standings(con, guild_id):
    rows = con.execute("""
        WITH earned AS (
            SELECT user_id, SUM(coins_awarded) AS total
            FROM roll_coin_awards WHERE guild_id=? GROUP BY user_id
        ), spent AS (
            SELECT user_id, COUNT(*) AS lost
            FROM roll_coin_gambles
            WHERE guild_id=? AND stake_returned=0 GROUP BY user_id
        )
        SELECT earned.user_id,
               earned.total - COALESCE(spent.lost, 0) AS coins
        FROM earned LEFT JOIN spent ON spent.user_id=earned.user_id
        WHERE earned.total > COALESCE(spent.lost, 0)
        ORDER BY coins DESC, earned.user_id ASC
    """, (guild_id, guild_id)).fetchall()
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
    if day != parsed.isoformat():
        raise ValueError("Use YYYY-MM-DD for the winning date.")
    if parsed >= now.astimezone(UK).date():
        raise ValueError("Choose a completed day, before today (UK time).")
    if not 1 <= value <= 100:
        raise ValueError("The winning roll must be between 1 and 100.")
    # Saved rolls take priority over a manual recovery for the same day.
    settle_completed_days(con, guild_id, now)
    result = con.execute("""
        INSERT OR IGNORE INTO roll_coin_awards
        (guild_id, roll_date, user_id, username, winning_roll, source, awarded_at, awarded_by,
         coins_awarded)
        VALUES (?, ?, ?, ?, ?, 'manual', ?, ?, ?)
    """, (guild_id, parsed.isoformat(), user_id, username, value,
          int(now.timestamp()), owner_id, daily_coin_prize(parsed)))
    return result.rowcount == 1
