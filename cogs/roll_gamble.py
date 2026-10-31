"""Atomic, coin-backed extra rolls for Hyped Ogre.

The canonical rolls table remains unchanged. A player must have their normal
free roll ('user') already recorded before one optional extra roll ('gamble').
The additional roll and stake ledger are committed atomically. Normal roll
history and earlier coin awards are untouched.
"""
from datetime import datetime, time, timedelta

from cogs.roll_coins import UK, coin_total


class GambleError(ValueError):
    """Safe, user-visible reason that a gamble was refused."""


def gamble_once(con, guild_id, user_id, username, *,
                extra_value, roll_writer, now=None):
    """Spend one coin for exactly one extra roll on this UK calendar day.

    The member must choose to make their free roll first. Gambling never
    generates a free roll. The stake is returned if and only if the extra
    roll beats their original free roll; equal or lower costs one coin.
    The higher roll enters the normal daily competition, with no new coin
    granted just for gambling.

    A BEGIN IMMEDIATE write lock serialises the balance check across processes.
    The savepoints in roll_writer are nested and rolled back with this action.
    """
    now = now or datetime.now(UK)
    now = now.astimezone(UK)
    if (not isinstance(extra_value, int) or isinstance(extra_value, bool)
            or not 1 <= extra_value <= 100):
        raise GambleError("Roll values must be between 1 and 100.")
    day = now.date().isoformat()
    start = int(datetime.combine(now.date(), time.min, UK).timestamp())
    end = int(datetime.combine(now.date() + timedelta(days=1), time.min, UK).timestamp())
    stamp = int(now.timestamp())

    con.execute("BEGIN IMMEDIATE")
    try:
        existing_gamble = con.execute("""
            SELECT 1 FROM roll_coin_gambles
            WHERE guild_id=? AND roll_date=? AND user_id=?
        """, (guild_id, day, user_id)).fetchone()
        if existing_gamble:
            raise GambleError("Ogre only take one gamble per day.")
        if coin_total(con, guild_id, user_id) < 1:
            raise GambleError("Ogre need one shiny coin before gamble.")

        original = con.execute("""
            SELECT value FROM rolls
            WHERE actor_type='user' AND user_id=?
              AND rolled_at >= ? AND rolled_at < ?
            ORDER BY rolled_at ASC, rowid ASC LIMIT 1
        """, (user_id, start, end)).fetchone()
        if original is None:
            raise GambleError("Ogre say roll first. Then decide if coin worth gamble.")
        base = original[0]

        if not roll_writer(user_id, username, extra_value, "gamble", timestamp=stamp):
            raise GambleError("Ogre only take one gamble per day.")

        returned = extra_value > base
        con.execute("""
            INSERT INTO roll_coin_gambles
                (guild_id, roll_date, user_id, base_roll, gamble_roll,
                 stake_returned, played_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, day, user_id, base, extra_value, int(returned), stamp))
        balance = coin_total(con, guild_id, user_id)
        if balance < 0:
            raise GambleError("Ogre cannot spend coin you don't have.")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return {
        "base": base,
        "extra": extra_value,
        "best": max(base, extra_value),
        "stake_returned": returned,
        "balance": balance,
        "date": day,
    }
