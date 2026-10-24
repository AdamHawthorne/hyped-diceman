# -----------------------------------------
# Hyped Ogre – daily roll Discord bot for the Hyped community
# Derived from RollF by hegernat/plutonium87
# Original source: https://github.com/hegernat/rollf-bot
# Copyright (c) 2026 hegernat/plutonium87
# Modifications Copyright (c) 2026 AdamHawthorne
# Licensed under the MIT License. See LICENSE.
# -----------------------------------------

import threading
import aiohttp
import asyncio
import sqlite3
import secrets
import time
import csv
import io
import os
import discord
import logging
import json
import hmac
import hashlib
from logging.handlers import TimedRotatingFileHandler
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont


# ---------------- CONFIG ----------------

load_dotenv()

TZ = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/London"))
BOT_NAME = os.getenv("BOT_NAME", "Hyped Ogre")
DB_PATH = os.getenv("DB_PATH", "bot.db")

HYPED_GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
ROLL_CHANNEL_ID = int(os.getenv("ROLL_CHANNEL_ID", "0"))
HIGH_ROLLER_ROLE_ID = int(os.getenv("HIGH_ROLLER_ROLE_ID", "0"))
FOREVER_ROLE_ID = int(os.getenv("FOREVER_ROLE_ID", "0"))
FLAMEBOARD_CHANNEL_ID = int(os.getenv("FLAMEBOARD_CHANNEL_ID", "0"))
FLAMEBOARD_THRESHOLD = int(os.getenv("FLAMEBOARD_THRESHOLD", "3"))
FLAMEBOARD_CAST_URL = "https://i.vgy.me/nS4L0z.png"

BOTLIST_COMMANDS = [
    {"command": "roll", "description": "Roll your daily number (1–100)"},
    {"command": "leaderboards", "description": "View rankings for different periods"},
    {"command": "stats", "description": "View detailed statistics"},
    {"command": "setchannel", "description": "Set the channel for daily bot rolls"},
    {"command": "help", "description": "Show setup instructions"}
]

ENABLE_COMMAND_LOGGING = (
    os.getenv("ENABLE_COMMAND_LOGGING", "false").lower()
    == "true"
)
LOG_RETENTION_DAYS = 30
DAILY_ROLL_TASK = None
CROWN_RESET_TASK = None
DAILY_POST_TASK = None

VOTE_COLORS = [
    (255, 255, 204),  # Pale Yellow
    (255, 204, 255),  # Pale Pink
    (204, 153, 255),  # Purple
    (204, 204, 255),  # Lavender
    (153, 204, 255),  # Blue
    (204, 255, 255),  # Cyan
    (153, 255, 204),  # Mint Green
    (204, 255, 204),  # Pale Green
    (204, 255, 153),  # Lime
]

# ---------------- ADMIN ----------------

ADMIN_MODE = os.getenv("ADMIN_MODE") == "true"
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_GUILD_ID = int(os.getenv("ADMIN_GUILD_ID", "0"))
TOKEN = os.getenv("DISCORD_TOKEN")
BOTLIST_TOKEN = os.getenv("BOTLIST_TOKEN")
TOPGG_TOKEN = os.getenv("TOPGG_TOKEN")
TOPGG_WEBHOOK_SECRET = os.getenv("TOPGG_WEBHOOK_SECRET")

ONBOARDING_TEXT = (
    "**Hyped Ogre is ready.**\n\n"
    "Use `/roll` once per day, `/leaderboards` for rankings, "
    "and `/stats` for your roll history."
)

# ---------------- DB ----------------

async def post_bot_stats():

    guild_count = len(bot.guilds)
    bot_id = bot.user.id

    async with aiohttp.ClientSession() as session:

        if BOTLIST_TOKEN:
            try:
                async with session.post(
                    f"https://discordbotlist.com/api/v1/bots/{bot_id}/stats",
                    json={"guilds": guild_count},
                    headers={"Authorization": BOTLIST_TOKEN},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    await resp.read()
            except Exception:
                log_error("BOTLIST STATS UPDATE FAILED")

        if TOPGG_TOKEN:
            try:
                async with session.post(
                    f"https://top.gg/api/bots/{bot_id}/stats",
                    json={"server_count": guild_count},
                    headers={"Authorization": TOPGG_TOKEN},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    await resp.read()
            except Exception:
                log_error("TOPGG STATS UPDATE FAILED")

async def post_botlist_commands():

    if not BOTLIST_TOKEN:
        return

    bot_id = bot.user.id

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"https://discordbotlist.com/api/v1/bots/{bot_id}/commands",
                json=BOTLIST_COMMANDS,
                headers={"Authorization": BOTLIST_TOKEN},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                await resp.read()
        except Exception:
            log_error(
                "BOTLIST COMMAND UPDATE FAILED"
            )

async def topgg_webhook(request):

    raw_body = await request.text()

    signature = request.headers.get("X-Topgg-Signature")

    if not TOPGG_WEBHOOK_SECRET:
        log_error("TOPGG_WEBHOOK_SECRET is missing")
        return web.Response(
            status=500,
            text="Server configuration error"
        )

    if not signature:
        log_event("TOPGG MISSING SIGNATURE")
        return web.Response(
            status=401,
            text="Unauthorized"
        )

    try:
        parts = dict(
            item.split("=", 1)
            for item in signature.split(",")
        )

        timestamp = parts["t"]
        received_sig = parts["v1"]

    except Exception:
        log_event("TOPGG BAD SIGNATURE HEADER")
        return web.Response(
            status=401,
            text="Unauthorized"
        )

    expected_sig = hmac.new(
        TOPGG_WEBHOOK_SECRET.encode(),
        f"{timestamp}.{raw_body}".encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, received_sig):
        log_event(
            f"TOPGG INVALID SIGNATURE ip={request.remote}"
        )
        return web.Response(
            status=401,
            text="Unauthorized"
        )

    if abs(time.time() - int(timestamp)) > 300:
        log_event(f"TOPGG EXPIRED SIGNATURE ip={request.remote}")
        return web.Response(
           status=401,
           text="Unauthorized"
        )

    data = json.loads(raw_body)

    event_type = data.get("type")

    if event_type == "webhook.test":
        return web.Response(text="OK")

    if event_type != "vote.create":
        log_event(f"TOPGG UNKNOWN EVENT {event_type}")
        return web.Response(text="OK")

    user_id = int(data["data"]["user"]["platform_id"])

    expires = (
        datetime.fromisoformat(
            data["data"]["expires_at"]
        ) + timedelta(hours=12)
    )

    valid_until = int(expires.timestamp())

    with db() as con:

        old = con.execute("""
            SELECT color_index
            FROM topgg_votes
            WHERE user_id = ?
        """, (user_id,)).fetchone()

        old_index = old["color_index"] if old else None

        available = list(range(len(VOTE_COLORS)))

        if old_index is not None and old_index in available:
            available.remove(old_index)

        color_index = secrets.choice(available)

        con.execute("""
            INSERT INTO topgg_votes
                (user_id, valid_until, color_index)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                valid_until=excluded.valid_until,
                color_index=excluded.color_index
        """, (user_id, valid_until, color_index))

    log_event(f"TOPGG VOTE user={user_id} color={color_index}")

    return web.Response(text="OK")

async def start_webhook():

    app = web.Application()

    async def remove_server_header(request, response):
        response.headers.pop("Server", None)

    app.on_response_prepare.append(remove_server_header)

    app.router.add_post(
        "/topgg",
        topgg_webhook
    )

    app.router.add_get(
        "/privacy",
        lambda request: web.FileResponse("web/privacy.html")
    )

    app.router.add_get(
        "/terms",
        lambda request: web.FileResponse("web/terms.html")
    )

    app.router.add_get(
        "/static/rollf.css",
        lambda request: web.FileResponse("web/rollf.css")
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        8080
    )

    await site.start()

def ensure_schema():

    with db() as con:

        # Upstream RollF deployments may already have these tables in bot.db.
        # Creating them here makes a fresh clone safe to start from an empty DB.
        con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS rolls (
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            value INTEGER NOT NULL,
            rolled_at INTEGER NOT NULL,
            actor_type TEXT NOT NULL,
            roll_date TEXT
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS guild_channels (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            set_at INTEGER NOT NULL
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS guild_meta (
            guild_id INTEGER PRIMARY KEY,
            onboarding_sent INTEGER NOT NULL DEFAULT 0
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS crown_state (
            guild_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            roll_date TEXT NOT NULL
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS daily_reset_posts (
            roll_date TEXT PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS flameboard_posts (
            source_message_id INTEGER PRIMARY KEY,
            source_channel_id INTEGER NOT NULL,
            flameboard_message_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
        """)

        cols = con.execute("PRAGMA table_info(rolls)").fetchall()
        names = {c[1] for c in cols}

        if "roll_date" not in names:
            con.execute("ALTER TABLE rolls ADD COLUMN roll_date TEXT")

        con.execute("""
        UPDATE rolls
        SET roll_date = date(rolled_at, 'unixepoch')
        WHERE roll_date IS NULL
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS daily_scores (
            user_id INTEGER,
            roll_date TEXT,
            score INTEGER NOT NULL,
            rolls INTEGER NOT NULL,
            PRIMARY KEY(user_id, roll_date)
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS topgg_votes (
            user_id INTEGER PRIMARY KEY,
            valid_until INTEGER NOT NULL,
            color_index INTEGER NOT NULL
        )
        """)

        count = con.execute(
            "SELECT COUNT(*) FROM daily_scores"
        ).fetchone()[0]

        if count == 0:
            con.execute("""
            INSERT INTO daily_scores (user_id, roll_date, score, rolls)
            SELECT
                user_id,
                roll_date,
                SUM(value),
                COUNT(*)
            FROM rolls
            WHERE actor_type = 'user'
              AND roll_date IS NOT NULL
            GROUP BY user_id, roll_date
            """)

_local = threading.local()

def db():
    con = getattr(_local, "con", None)

    if con is None:
        con = sqlite3.connect(
            DB_PATH,
            timeout=30,
            isolation_level=None
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("PRAGMA foreign_keys=ON")
        _local.con = con

    return con

def ensure_indexes():
    with db() as con:

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_guild_channels_guild
        ON guild_channels(guild_id)
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_guild_channels_channel
        ON guild_channels(channel_id)
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_rolls_actor_date
        ON rolls(actor_type, roll_date)
        """)

        con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_roll_per_day
        ON rolls(user_id, roll_date)
        WHERE actor_type='user'
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_scores_date
        ON daily_scores(roll_date)
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_rolls_actor_time_user
        ON rolls(actor_type, rolled_at, user_id)
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS user_scores (
            user_id INTEGER PRIMARY KEY,
            score INTEGER NOT NULL DEFAULT 0,
            rolls INTEGER NOT NULL DEFAULT 0,
            best INTEGER NOT NULL DEFAULT 0
        )
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_scores_score
        ON user_scores(score DESC)
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_rolls_user_time
        ON rolls(user_id, rolled_at)
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_rolls_time
        ON rolls(rolled_at)
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_rolls_user_period
        ON rolls(rolled_at, user_id)
        WHERE actor_type='user'
        """)

        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_rolls_user_date
        ON rolls(user_id, roll_date)
        WHERE actor_type='user'
        """)

        count = con.execute("SELECT COUNT(*) FROM user_scores").fetchone()[0]

        if count == 0:
            con.execute("""
            INSERT INTO user_scores (user_id, score, rolls, best)
            SELECT
                user_id,
                SUM(value),
                COUNT(*),
                MAX(value)
            FROM rolls
            WHERE actor_type = 'user'
            GROUP BY user_id
            """)

            print("Backfilled user_scores from rolls table")

def today_range():
    now = datetime.now(TZ)
    start = datetime(now.year, now.month, now.day, tzinfo=TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())

def bot_rolled_today():
    start, end = today_range()
    with db() as con:
        cur = con.execute(
            """SELECT 1 FROM rolls
               WHERE actor_type='bot'
               AND rolled_at BETWEEN ? AND ?
               LIMIT 1""",
            (start, end)
        )
        return cur.fetchone() is not None

def upsert_user(user_id: int, username: str):
    now = int(time.time())
    with db() as con:
        con.execute(
            """
            INSERT INTO users (user_id, username, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                username = excluded.username,
                updated_at = excluded.updated_at
            """,
            (user_id, username, now)
        )

def insert_roll(user_id, username, value, actor_type):

    ts = int(time.time())
    roll_date = datetime.fromtimestamp(ts, TZ).date().isoformat()

    with db() as con:

        try:
            con.execute(
                """INSERT INTO rolls (user_id, username, value, rolled_at, actor_type, roll_date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, username, value, ts, actor_type, roll_date)
            )
        except sqlite3.IntegrityError:
            return False

        if actor_type == 'user':
            con.execute("""
            INSERT INTO user_scores (user_id, score, rolls, best)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                score = score + excluded.score,
                rolls = rolls + 1,
                best = MAX(best, excluded.best)
            """, (user_id, value, value))

            con.execute("""
            INSERT INTO daily_scores (user_id, roll_date, score, rolls)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, roll_date) DO UPDATE SET
                score = score + excluded.score,
                rolls = rolls + 1
            """, (user_id, roll_date, value))

    return True

def setup_logging():

    if not ENABLE_COMMAND_LOGGING:
        return None

    os.makedirs("logs", exist_ok=True)

    handler = TimedRotatingFileHandler(
        filename="logs/rollf.log",
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8"
    )

    handler.suffix = "%Y%m%d"

    def custom_log_namer(default_name):
        base = os.path.basename(default_name)

        if ".log." in base:
            date_part = base.split(".log.")[1]
            return os.path.join(
                "logs",
                f"rollf_{date_part}.log"
            )

        return default_name

    handler.namer = custom_log_namer

    formatter = logging.Formatter(
        "[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler.setFormatter(formatter)

    logger = logging.getLogger("rollf")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        logger.addHandler(handler)

    return logger

def log_command(
    interaction: discord.Interaction,
    command_name: str
):
    if not LOGGER:
        return

    LOGGER.info(
        "COMMAND %s user=%s",
        command_name,
        interaction.user.id
    )

LOGGER = setup_logging()

def log_event(message: str):
    if LOGGER:
        LOGGER.info(message)


def log_error(message: str):
    if LOGGER:
        LOGGER.exception(message)

def trim(name, max_len=18):
    return name[:max_len-1] + "…" if len(name) > max_len else name

def get_user_stats(user_id: int):
    with db() as con:
        total = con.execute(
            """
            SELECT
                COUNT(*) AS rolls,
                SUM(value) AS score,
                MAX(value) AS best,
                AVG(value) AS avg,
                SUM(CASE WHEN value = 100 THEN 1 ELSE 0 END) AS perfects
            FROM rolls
            WHERE user_id = ?
            AND actor_type = 'user'
            """,
            (user_id,)
        ).fetchone()

        last10 = con.execute(
            """
            SELECT AVG(value)
            FROM (
                SELECT value
                FROM rolls
                WHERE user_id = ?
                  AND actor_type = 'user'
                ORDER BY rolled_at DESC
                LIMIT 10
            )
            """,
            (user_id,)
        ).fetchone()[0]

        rank = con.execute(
            """
            SELECT COUNT(*) + 1
            FROM user_scores
            WHERE score >
            (
                SELECT score
                FROM user_scores
                WHERE user_id = ?
            )
            """,
            (user_id,)
        ).fetchone()[0]

        first_roll = con.execute("""
            SELECT MIN(rolled_at)
            FROM rolls
            WHERE user_id = ?
            AND actor_type = 'user'
        """, (user_id,)).fetchone()[0]

    return {
        "rolls": total[0] or 0,
        "score": total[1] or 0,
        "best": total[2] or 0,
        "avg": float(total[3]) if total[3] else 0.0,
        "perfects": total[4] or 0,
        "avg10": float(last10) if last10 else 0.0,
        "first_roll": first_roll,
        "rank": rank
    }

def calculate_streaks(user_id: int):

    with db() as con:
        rows = con.execute("""
            SELECT DISTINCT roll_date
            FROM rolls
            WHERE user_id = ?
              AND actor_type = 'user'
            ORDER BY roll_date
        """, (user_id,)).fetchall()

    if not rows:
        return 0, 0

    dates = [datetime.fromisoformat(r[0]).date() for r in rows if r[0]]

    best = 0
    current_run = 0
    prev_date = None

    for d in dates:

        if prev_date is None:
            current_run = 1

        elif d == prev_date + timedelta(days=1):
            current_run += 1

        else:
            current_run = 1

        best = max(best, current_run)
        prev_date = d

    today = datetime.now(TZ).date()

    date_set = set(dates)

    if today not in date_set:
        current = 0
    else:
        current = 1
        check_day = today

        while True:
            check_day -= timedelta(days=1)

            if check_day in date_set:
                current += 1
            else:
                break

    return current, best

def get_period_stats(user_id: int, start_ts: int, end_ts: int):
    with db() as con:
        total = con.execute("""
            SELECT COUNT(*), SUM(value), MAX(value)
            FROM rolls
            WHERE user_id = ?
              AND actor_type = 'user'
              AND rolled_at BETWEEN ? AND ?
        """, (user_id, start_ts, end_ts)).fetchone()

        rank = con.execute("""
            SELECT COUNT(*) + 1
            FROM (
                SELECT user_id, SUM(value) AS score
                FROM rolls
                WHERE actor_type = 'user'
                  AND rolled_at BETWEEN ? AND ?
                GROUP BY user_id
            )
            WHERE score > (
                SELECT SUM(value)
                FROM rolls
                WHERE user_id = ?
                  AND actor_type = 'user'
                  AND rolled_at BETWEEN ? AND ?
            )
        """, (start_ts, end_ts, user_id, start_ts, end_ts)).fetchone()[0]

    return {
        "rolls": total[0] or 0,
        "score": total[1] or 0,
        "best": total[2] or 0,
        "rank": None if (total[0] or 0) == 0 else rank
    }

def format_score(n: int) -> str:
    return f"{n:,}"

def render_leaderboard_png(
    title,
    rows,
    users_count=0,
    rolls_count=0,
    reset_text=None,
    score_label="SCORE"
):

    import io

    width = 640

    top_padding = 20
    header_y = 120
    row_height = 48

    height = 820

    img = Image.new(
        "RGB",
        (width, height),
        (35, 39, 42)
    )

    draw = ImageDraw.Draw(img)

    title_font = ImageFont.truetype(
        "assets/fonts/JetBrainsMono-Regular.ttf",
        38
    )

    body_font = ImageFont.truetype(
        "assets/fonts/JetBrainsMono-Regular.ttf",
        30
    )

    RANK_X = 20
    USER_X = 95
    SCORE_RIGHT = 620

    draw.text(
        (20, top_padding),
        title,
        fill=(255, 255, 255),
        font=title_font
    )

    draw.text(
        (RANK_X, header_y),
        "#",
        fill=(180, 180, 180),
        font=body_font
    )

    draw.text(
        (USER_X, header_y),
        "USER",
        fill=(180, 180, 180),
        font=body_font
    )

    bbox = draw.textbbox(
        (0, 0),
        score_label,
        font=body_font
    )

    label_width = bbox[2] - bbox[0]

    draw.text(
        (SCORE_RIGHT - label_width, header_y),
        score_label,
        fill=(180, 180, 180),
        font=body_font
    )

    draw.line(
        (20, header_y + 35, 620, header_y + 35),
        fill=(90, 90, 90),
        width=2
    )

    y = header_y + 50

    for pos, (username, score, uid) in enumerate(rows, start=1):

        username_color = get_vote_color(uid)

        if username_color is None:
            username_color = (255, 255, 255)

        if pos % 2 == 0:
            draw.rectangle(
                (
                    10,
                    y + 1,
                    630,
                    y + 39
                ),
                fill=(42, 45, 50)
            )

        draw.text(
            (RANK_X, y),
            str(pos),
            fill=(255, 255, 255),
            font=body_font
        )

        draw.text(
            (USER_X, y),
            trim(username, 18),
            fill=username_color,
            font=body_font
        )

        if score == "—":
            score_text = "—"
        else:
            score_text = format_score(score)

        bbox = draw.textbbox(
            (0, 0),
            score_text,
            font=body_font
        )

        score_width = bbox[2] - bbox[0]

        draw.text(
            (SCORE_RIGHT - score_width, y),
            score_text,
            fill=(255, 255, 255),
            font=body_font
        )

        y += row_height

    stats_font = ImageFont.truetype(
        "assets/fonts/JetBrainsMono-Regular.ttf",
        26
    )

    draw.text(
        (20, 710),
        f"Users: {users_count:,}",
        fill=(180, 180, 180),
        font=stats_font
    )

    if rolls_count is not None:
        draw.text(
            (20, 745),
            f"Rolls: {rolls_count:,}",
            fill=(180, 180, 180),
            font=stats_font
        )

    if reset_text:
        draw.text(
            (20, 780),
            reset_text,
            fill=(180, 180, 180),
            font=stats_font
        )

    buffer = io.BytesIO()

    img.save(
        buffer,
        format="PNG"
    )

    buffer.seek(0)

    return buffer

def get_bot_stats():

    with db() as con:

        today_roll = con.execute("""
            SELECT value
            FROM rolls
            WHERE actor_type = 'bot'
            AND roll_date = ?
            LIMIT 1
        """, (
            datetime.now(TZ).date().isoformat(),
        )).fetchone()

        total = con.execute("""
            SELECT
                COUNT(*) AS rolls,
                SUM(value) AS score,
                MAX(value) AS best,
                MIN(value) AS worst,
                AVG(value) AS avg,
                SUM(CASE WHEN value = 100 THEN 1 ELSE 0 END) AS perfects
            FROM rolls
            WHERE actor_type = 'bot'
        """).fetchone()

        first_roll = con.execute("""
            SELECT MIN(rolled_at)
            FROM rolls
            WHERE actor_type = 'bot'
        """).fetchone()[0]

        last10 = con.execute("""
             SELECT AVG(value)
                FROM (
                SELECT value
                FROM rolls
                WHERE actor_type = 'bot'
                ORDER BY rolled_at DESC
                LIMIT 10
            )
        """).fetchone()[0]

    return {
        "rolls": total[0] or 0,
        "score": total[1] or 0,
        "today_roll": today_roll[0] if today_roll else None,
        "best": total[2] or 0,
        "worst": total[3] or 0,
        "avg": float(total[4]) if total[4] else 0.0,
        "perfects": total[5] or 0,
        "first_roll": first_roll,
        "avg10": float(last10) if last10 else 0.0
    }

def get_time_left():
    now = datetime.now(TZ)
    midnight = datetime(
        now.year, now.month, now.day, tzinfo=TZ
    ) + timedelta(days=1)

    remaining = max(
        0, int((midnight - now).total_seconds())
    )

    if remaining < 3600:
        minutes = -(-remaining // 60)
        return f"{minutes}m"

    hours = remaining // 3600
    minutes = -(- (remaining % 3600) // 60)

    if minutes == 60:
        hours += 1
        minutes = 0

    if minutes == 0:
        return f"{hours}h"

    return f"{hours}h {minutes}m"

def get_vote_color(user_id):

    now = int(time.time())

    with db() as con:

        row = con.execute("""
            SELECT color_index
            FROM topgg_votes
            WHERE user_id=?
              AND valid_until>?
        """, (user_id, now)).fetchone()

    if row is None:
        return None

    color_index = row["color_index"]

    if color_index >= len(VOTE_COLORS):
        color_index %= len(VOTE_COLORS)

    return VOTE_COLORS[color_index]

# ---------------- BOT SETUP ----------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="diceman_", intents=intents)

async def fetch_guild_member(guild: discord.Guild, user_id: int):
    member = guild.get_member(user_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None

async def remove_crown_from_user(guild: discord.Guild, role: discord.Role, user_id: int):
    member = await fetch_guild_member(guild, user_id)
    if member is None or role not in member.roles:
        return

    try:
        await member.remove_roles(role, reason="Daily High Roller reset")
    except (discord.Forbidden, discord.HTTPException):
        log_error(f"CROWN REMOVE FAILED user={user_id}")

async def clear_high_roller_role(extra_user_id: int | None = None):
    if not HYPED_GUILD_ID or not HIGH_ROLLER_ROLE_ID:
        return

    guild = bot.get_guild(HYPED_GUILD_ID)
    if guild is None:
        return

    role = guild.get_role(HIGH_ROLLER_ROLE_ID)
    if role is None:
        log_event("CROWN ROLE NOT FOUND")
        return

    with db() as con:
        state = con.execute(
            "SELECT user_id FROM crown_state WHERE guild_id = ?",
            (HYPED_GUILD_ID,)
        ).fetchone()

    # Normally crown_state identifies the holder. Also remove the role from any
    # cached holder and, for a manual reset, the invoking owner. This handles a
    # stale Discord role left behind when moving to a fresh database.
    user_ids = {member.id for member in role.members}

    if state:
        user_ids.add(state["user_id"])

    if extra_user_id:
        user_ids.add(extra_user_id)

    for user_id in user_ids:
        await remove_crown_from_user(guild, role, user_id)

    with db() as con:
        con.execute(
            "DELETE FROM crown_state WHERE guild_id = ?",
            (HYPED_GUILD_ID,)
        )

async def sync_high_roller_role():
    if not HYPED_GUILD_ID or not HIGH_ROLLER_ROLE_ID:
        return

    guild = bot.get_guild(HYPED_GUILD_ID)
    if guild is None:
        return

    role = guild.get_role(HIGH_ROLLER_ROLE_ID)
    if role is None:
        log_event("CROWN ROLE NOT FOUND")
        return

    start, end = today_range()
    today = datetime.now(TZ).date().isoformat()

    with db() as con:
        champion = con.execute(
            """
            SELECT user_id, value
            FROM rolls
            WHERE actor_type = 'user'
              AND rolled_at BETWEEN ? AND ?
            ORDER BY value DESC, rolled_at ASC
            LIMIT 1
            """,
            (start, end)
        ).fetchone()

        state = con.execute(
            """
            SELECT user_id, score, roll_date
            FROM crown_state
            WHERE guild_id = ?
            """,
            (HYPED_GUILD_ID,)
        ).fetchone()

    if champion is None:
        if state is not None:
            await remove_crown_from_user(guild, role, state["user_id"])
            with db() as con:
                con.execute(
                    "DELETE FROM crown_state WHERE guild_id = ?",
                    (HYPED_GUILD_ID,)
                )
        return

    champion_id = champion["user_id"]
    champion_score = champion["value"]

    if (
        state is not None
        and state["roll_date"] == today
        and state["user_id"] == champion_id
        and state["score"] == champion_score
    ):
        return

    if state is not None and state["user_id"] != champion_id:
        await remove_crown_from_user(guild, role, state["user_id"])

    champion_member = await fetch_guild_member(guild, champion_id)
    if champion_member is None:
        log_event(f"CROWN MEMBER NOT FOUND user={champion_id}")
        return

    if role not in champion_member.roles:
        try:
            await champion_member.add_roles(role, reason="Current Daily High Roller")
        except (discord.Forbidden, discord.HTTPException):
            log_error(f"CROWN ADD FAILED user={champion_id}")
            return

    with db() as con:
        con.execute(
            """
            INSERT INTO crown_state (guild_id, user_id, score, roll_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                user_id = excluded.user_id,
                score = excluded.score,
                roll_date = excluded.roll_date
            """,
            (HYPED_GUILD_ID, champion_id, champion_score, today)
        )

    log_event(f"CROWN HOLDER user={champion_id} score={champion_score}")

class DailyRollView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Roll",
        emoji="🎲",
        style=discord.ButtonStyle.primary,
        custom_id="hyped_diceman_daily_roll"
    )
    async def roll_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        log_command(interaction, "daily-roll-button")
        await perform_roll(interaction)


class ForeverRoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Forever Role",
        style=discord.ButtonStyle.secondary,
        custom_id="hyped_ogre_forever_role"
    )
    async def forever_role_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        guild = interaction.guild

        if guild is None or guild.id != HYPED_GUILD_ID:
            await interaction.response.send_message(
                "This role button is only available in Hyped.",
                ephemeral=True
            )
            return

        role = guild.get_role(FOREVER_ROLE_ID)

        if role is None:
            await interaction.response.send_message(
                "The Forever role is not configured correctly.",
                ephemeral=True
            )
            return

        member = interaction.user

        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "I couldn't update your role.",
                ephemeral=True
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(
                    role,
                    reason="Forever role removed via Hyped Ogre role panel"
                )
                await interaction.response.send_message(
                    "**Forever** role removed.",
                    ephemeral=True
                )
                log_event(f"FOREVER ROLE REMOVED user={member.id}")
            else:
                await member.add_roles(
                    role,
                    reason="Forever role added via Hyped Ogre role panel"
                )
                await interaction.response.send_message(
                    (
                        "**Forever** role added. By taking the role, you agree to:\n"
                        "• keep the atmosphere positive and enjoyable\n"
                        "• show up prepared and focused\n"
                        "• be reliable and communicate clearly\n"
                        "• no racism or hate of any kind"
                    ),
                    ephemeral=True
                )
                log_event(f"FOREVER ROLE ADDED user={member.id}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can't manage the Forever role. Check my role hierarchy and Manage Roles permission.",
                ephemeral=True
            )


def build_forever_role_embed():
    embed = discord.Embed(
        title="<Hyped> for WoW: Forever.",
        description=(
            "**Click the button below** to get access to the Forever channels.\n\n"
            "Click it again at any time to remove the role."
        ),
        color=0x8C5A3C
    )

    embed.set_thumbnail(url="https://i.vgy.me/asI88C.png")
    embed.set_image(url="https://i.vgy.me/WGNGdr.png")

    return embed


def build_daily_reset_embed():
    embed = discord.Embed(
        title="Hyped Rolls - Daily Reset",
        description=(
            "Competitive rolling for fun.\n\n"
            "Use the **🎲 Roll** button below or `/roll` once per day.\n\n"
            "`/leaderboards` - top rollers\n"
            "`/stats` - your stats\n\n"
            "**Are you the 🎲 Luckiest Gamer (today)?**"
        ),
        color=0xD19A3B
    )

    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    return embed


async def post_daily_reset_message():
    channel = bot.get_channel(ROLL_CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(ROLL_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            log_error(f"DAILY RESET CHANNEL LOOKUP FAILED channel={ROLL_CHANNEL_ID} error={exc!r}")
            return

    today = datetime.now(TZ).date().isoformat()

    with db() as con:
        existing = con.execute(
            "SELECT message_id FROM daily_reset_posts WHERE roll_date = ?",
            (today,)
        ).fetchone()

    if existing:
        log_event(f"DAILY RESET ALREADY RECORDED date={today} message={existing[0]}")
        return

    embed = build_daily_reset_embed()

    try:
        message = await channel.send(
            embed=embed,
            view=DailyRollView()
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        log_error(f"DAILY RESET POST FAILED channel={ROLL_CHANNEL_ID} error={exc!r}")
        return

    with db() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO daily_reset_posts
                (roll_date, channel_id, message_id)
            VALUES (?, ?, ?)
            """,
            (today, channel.id, message.id)
        )

    log_event(f"DAILY RESET POST message={message.id}")


async def daily_post_loop():
    """Post the Daily Roll panel at 00:10 Europe/London for testing."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = datetime.now(TZ)
            next_post = now.replace(hour=0, minute=10, second=0, microsecond=0)
            if now >= next_post:
                next_post = (now + timedelta(days=1)).replace(
                    hour=0, minute=10, second=0, microsecond=0
                )
            await asyncio.sleep(max(1, (next_post - now).total_seconds()))
            await post_daily_reset_message()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(f"DAILY POST SCHEDULER ERROR: {exc!r}")
            await asyncio.sleep(30)


async def crown_reset_loop():
    """Keep High Roller role cleanup at midnight UK time."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = datetime.now(TZ)
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=2, microsecond=0
            )
            await asyncio.sleep(max(1, (tomorrow - now).total_seconds()))
            try:
                await clear_high_roller_role()
                log_event("CROWN RESET")
            except Exception as exc:
                log_error(f"CROWN RESET ERROR: {exc!r}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(f"MIDNIGHT SCHEDULER ERROR: {exc!r}")
            await asyncio.sleep(30)


async def get_flameboard_cast_file():
    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(FLAMEBOARD_CAST_URL) as response:
            response.raise_for_status()
            image_bytes = await response.read()

    return discord.File(
        io.BytesIO(image_bytes),
        filename="hyped_ogre_flame.png"
    )


def build_flameboard_embed(message: discord.Message, reaction_counts: dict[str, int]):
    content = message.content.strip()

    if len(content) > 3300:
        content = content[:3297] + "..."

    channel_name = getattr(message.channel, "name", "channel")
    source_line = f"#{channel_name} • [Jump to message]({message.jump_url})"

    description_parts = []
    if content:
        description_parts.append(content)
    description_parts.append(source_line)

    embed = discord.Embed(
        description="\n\n".join(description_parts),
        color=0xD35400,
        timestamp=message.created_at
    )

    embed.set_author(name=message.author.display_name)
    embed.set_thumbnail(url=message.author.display_avatar.url)

    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        if content_type.startswith("image/"):
            embed.set_image(url=attachment.url)
            break

    footer_parts = []
    if reaction_counts.get("🔥", 0):
        footer_parts.append(f"🔥 {reaction_counts['🔥']}")
    if reaction_counts.get("😂", 0):
        footer_parts.append(f"😂 {reaction_counts['😂']}")
    if reaction_counts.get("⭐", 0):
        footer_parts.append(f"⭐ {reaction_counts['⭐']}")
    embed.set_footer(text=" • ".join(footer_parts))

    return embed


def get_flameboard_video_url(message: discord.Message):
    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        if content_type.startswith("video/"):
            return attachment.url
    return None


async def sync_flameboard_message(channel_id: int, message_id: int):
    if not FLAMEBOARD_CHANNEL_ID:
        return

    if channel_id == FLAMEBOARD_CHANNEL_ID:
        return

    source_channel = bot.get_channel(channel_id)

    if source_channel is None:
        try:
            source_channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    try:
        message = await source_channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    reaction_counts = {"🔥": 0, "😂": 0, "⭐": 0}

    for reaction in message.reactions:
        emoji = str(reaction.emoji)
        if emoji in reaction_counts:
            reaction_counts[emoji] = reaction.count

    qualifying_count = sum(reaction_counts.values())

    flameboard_channel = bot.get_channel(FLAMEBOARD_CHANNEL_ID)

    if flameboard_channel is None:
        try:
            flameboard_channel = await bot.fetch_channel(FLAMEBOARD_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log_error("FLAMEBOARD CHANNEL NOT FOUND")
            return

    with db() as con:
        existing = con.execute(
            """
            SELECT flameboard_message_id
            FROM flameboard_posts
            WHERE source_message_id = ?
            """,
            (message_id,)
        ).fetchone()

    if qualifying_count >= FLAMEBOARD_THRESHOLD:
        embed = build_flameboard_embed(message, reaction_counts)

        if existing:
            try:
                board_message = await flameboard_channel.fetch_message(
                    existing["flameboard_message_id"]
                )
                await board_message.edit(embed=embed)
                return
            except discord.NotFound:
                with db() as con:
                    con.execute(
                        "DELETE FROM flameboard_posts WHERE source_message_id = ?",
                        (message_id,)
                    )
            except (discord.Forbidden, discord.HTTPException):
                log_error("FLAMEBOARD UPDATE FAILED")
                return

        try:
            video_attachment = next(
                (
                    attachment
                    for attachment in message.attachments
                    if (attachment.content_type or "").startswith("video/")
                ),
                None
            )

            if video_attachment is not None:
                try:
                    video_file = await video_attachment.to_file()
                    board_message = await flameboard_channel.send(
                        embed=embed,
                        file=video_file
                    )
                except (discord.HTTPException, discord.NotFound):
                    board_message = await flameboard_channel.send(
                        content=video_attachment.url,
                        embed=embed
                    )
            else:
                cast_file = await get_flameboard_cast_file()
                board_message = await flameboard_channel.send(
                    embed=embed,
                    file=cast_file
                )

        except (discord.Forbidden, discord.HTTPException):
            log_error("FLAMEBOARD POST FAILED")
            return

        with db() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO flameboard_posts
                    (source_message_id, source_channel_id, flameboard_message_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (message_id, channel_id, board_message.id, int(time.time()))
            )

        log_event(
            f"FLAMEBOARD POST source={message_id} board={board_message.id} reactions={qualifying_count}"
        )

    elif existing:
        try:
            board_message = await flameboard_channel.fetch_message(
                existing["flameboard_message_id"]
            )
            await board_message.delete()
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException):
            log_error("FLAMEBOARD DELETE FAILED")
            return

        with db() as con:
            con.execute(
                "DELETE FROM flameboard_posts WHERE source_message_id = ?",
                (message_id,)
            )

        log_event(
            f"FLAMEBOARD REMOVED source={message_id} reactions={qualifying_count}"
        )


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id != HYPED_GUILD_ID:
        return

    if str(payload.emoji) not in ("🔥", "😂", "⭐"):
        return

    await sync_flameboard_message(payload.channel_id, payload.message_id)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.guild_id != HYPED_GUILD_ID:
        return

    if str(payload.emoji) not in ("🔥", "😂", "⭐"):
        return

    await sync_flameboard_message(payload.channel_id, payload.message_id)


# ---------------- EVENTS ----------------

@bot.event
async def on_guild_join(guild: discord.Guild):

    if HYPED_GUILD_ID and guild.id != HYPED_GUILD_ID:
        log_event(f"UNAUTHORISED GUILD JOIN guild={guild.id}")
        await guild.leave()
        return

    with db() as con:
        row = con.execute(
            "SELECT onboarding_sent FROM guild_meta WHERE guild_id=?",
            (guild.id,)
        ).fetchone()

        if row and row[0]:
            return

    channel = None
    for ch in guild.text_channels:
        perms = ch.permissions_for(guild.me)
        if perms.view_channel and perms.send_messages:
            channel = ch
            break

    if channel is None:
        return

    try:
        await channel.send(ONBOARDING_TEXT)
        with db() as con:
            con.execute(
                "INSERT OR REPLACE INTO guild_meta (guild_id, onboarding_sent) VALUES (?, 1)",
                (guild.id,)
            )
    except discord.Forbidden:
        pass

    await post_bot_stats()

@bot.event
async def on_guild_remove(guild: discord.Guild):
    with db() as con:
        con.execute("DELETE FROM guild_channels WHERE guild_id = ?", (guild.id,))
        con.execute("DELETE FROM guild_meta WHERE guild_id = ?", (guild.id,))

    await post_bot_stats()

@bot.event
async def on_ready():

    if LOGGER:
        LOGGER.info(
            f"BOT STARTED ({BOT_NAME})"
        )

    global DAILY_ROLL_TASK, CROWN_RESET_TASK, DAILY_POST_TASK

    ensure_schema()
    ensure_indexes()

    bot.add_view(DailyRollView())
    bot.add_view(ForeverRoleView())

    # Start time-sensitive background tasks immediately. Global Discord command
    # syncing can occasionally take long enough to cross midnight.
    if DAILY_ROLL_TASK is None or DAILY_ROLL_TASK.done():
        DAILY_ROLL_TASK = asyncio.create_task(bot_daily_roll())

    if CROWN_RESET_TASK is None or CROWN_RESET_TASK.done():
        CROWN_RESET_TASK = asyncio.create_task(crown_reset_loop())

    if DAILY_POST_TASK is None or DAILY_POST_TASK.done():
        DAILY_POST_TASK = asyncio.create_task(daily_post_loop())

    # Catch up only after the scheduled 00:10 UK posting time.
    # Before 00:10, the dedicated loop will post at the scheduled time.
    now_uk = datetime.now(TZ)
    if (now_uk.hour, now_uk.minute) >= (0, 10):
        try:
            await post_daily_reset_message()
        except Exception as exc:
            log_error(f"DAILY RESET CATCH-UP ERROR: {exc!r}")

    await post_botlist_commands()
    await post_bot_stats()

    if HYPED_GUILD_ID and ROLL_CHANNEL_ID:
        with db() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO guild_channels (guild_id, channel_id, set_at)
                VALUES (?, ?, ?)
                """,
                (HYPED_GUILD_ID, ROLL_CHANNEL_ID, int(time.time()))
            )

    print(f"{BOT_NAME} logging in...")

    # -------- Global command sync --------
    try:
        synced = await bot.tree.sync()

        print("Global commands synced.")

        log_event(
            f"COMMANDS SYNCED count={len(synced)}"
        )

    except Exception:
        log_error(
            "GLOBAL COMMAND SYNC FAILED"
        )

    # -------- Admin guild sync (safe) --------
    if ADMIN_MODE and ADMIN_GUILD_ID:
        guild_obj = bot.get_guild(ADMIN_GUILD_ID)

        if guild_obj:
            try:
                await bot.tree.sync(guild=guild_obj)
                print("Admin guild commands synced.")
            except discord.Forbidden:
                print("Admin guild sync failed: Missing Access")
            except Exception as e:
                print("Admin guild sync error:", e)
        else:
            print("Admin guild not found in bot.guilds")

    if not ADMIN_MODE:
        print("Admin mode disabled.")

    # -------- Cleanup stale guilds --------
    current_ids = {g.id for g in bot.guilds}

    with db() as con:
        db_guilds = con.execute("""
            SELECT guild_id FROM guild_channels
            UNION
            SELECT guild_id FROM guild_meta
        """).fetchall()

        cleaned = 0

        for (gid,) in db_guilds:
            if gid not in current_ids:
                con.execute("DELETE FROM guild_channels WHERE guild_id = ?", (gid,))
                con.execute("DELETE FROM guild_meta WHERE guild_id = ?", (gid,))
                cleaned += 1

    if cleaned > 0:
        print(f"Cleaned {cleaned} stale guild entries")

    await sync_high_roller_role()

    if os.getenv("ENABLE_WEBHOOK") == "true":
        await start_webhook()

    log_event(
        f"BOT READY guilds={len(bot.guilds)}"
    )

    print(f"{BOT_NAME} online & ready.")

# ---------------- DAILY BOT ROLL ----------------

async def bot_daily_roll():
    await bot.wait_until_ready()

    while not bot.is_closed():
        now = datetime.now(TZ)

        if bot_rolled_today():
            tomorrow = (now + timedelta(days=1)).replace(hour=5, minute=55, second=0, microsecond=0)
            await asyncio.sleep((tomorrow - now).total_seconds())
            continue

        if now.hour >= 22:
            tomorrow = (now + timedelta(days=1)).replace(hour=5, minute=55, second=0, microsecond=0)
            await asyncio.sleep((tomorrow - now).total_seconds())
            continue

        if now.hour < 6:
            target = now.replace(hour=6, minute=0, second=0, microsecond=0)
            await asyncio.sleep((target - now).total_seconds())

        delay = secrets.randbelow(16 * 60 * 60)
        await asyncio.sleep(delay)

        if bot_rolled_today():
            continue

        value = secrets.randbelow(100) + 1

        if insert_roll(0, BOT_NAME, value, "bot"):
            log_event(
                f"BOT ROLL value={value}"
            )
        else:
            log_error(
                f"BOT ROLL FAILED value={value}"
            )

        with db() as con:
            rows = con.execute(
                "SELECT guild_id, channel_id FROM guild_channels"
            ).fetchall()

        for guild_id, channel_id in rows:

            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    try:
                        guild = bot.get_guild(guild_id)
                        if guild and guild.owner:
                            await guild.owner.send(
                                f"RollF could not access the configured channel on **{guild.name}**.\n"
                                f"Reason: missing permissions or role conflicts.\n"
                                f"Fix: give {BOT_NAME} explicit permissions in the selected channel "
                                f"(View Channel, Send Messages, Embed Links) and check category denies."
                            )
                    except Exception:
                        pass
                    continue
                except discord.HTTPException:
                    continue

            try:
                if value == 100:
                    await channel.send(f"{BOT_NAME} rolled 💯 🎲")
                else:
                    await channel.send(f"{BOT_NAME} rolled **{value}** 🎲")
            except discord.Forbidden:
                try:
                    guild = bot.get_guild(guild_id)
                    if guild and guild.owner:
                        await guild.owner.send(
                            f"{BOT_NAME} failed to send its daily roll in **{guild.name}**.\n"
                            f"Please check channel permissions."
                        )
                except Exception:
                    pass

# ---------------- COMMANDS ----------------

@bot.tree.command(
    name="stats",
    description="View detailed statistics for yourself or another user"
)
async def stats(
    interaction: discord.Interaction,
    user: discord.User | None = None
):
    log_command(interaction, "/stats")

    # Discord interactions must be acknowledged within a few seconds.
    # Defer immediately, then send the completed statistics as a follow-up.
    await interaction.response.defer()

    target = user or interaction.user

    is_bot_stats = (
        user is not None
        and user.id == bot.user.id
    )

    if is_bot_stats:
        stats = get_bot_stats()
    else:
        stats = get_user_stats(target.id)

    start, end = today_range()

    week_start = now = datetime.now(TZ)
    week_start = week_start - timedelta(days=week_start.weekday())
    week_start = datetime(week_start.year, week_start.month, week_start.day, tzinfo=TZ)
    week_end = week_start + timedelta(days=7)

    month_start = datetime(now.year, now.month, 1, tzinfo=TZ)
    if now.month == 12:
        month_end = datetime(now.year + 1, 1, 1, tzinfo=TZ)
    else:
        month_end = datetime(now.year, now.month + 1, 1, tzinfo=TZ)

    week_stats = get_period_stats(target.id, int(week_start.timestamp()), int(week_end.timestamp()))
    month_stats = get_period_stats(target.id, int(month_start.timestamp()), int(month_end.timestamp()))
    current_streak, best_streak = calculate_streaks(target.id)

    with db() as con:
        today_row = con.execute(
            """
            SELECT value
            FROM rolls
            WHERE user_id = ?
              AND actor_type = 'user'
              AND rolled_at BETWEEN ? AND ?
            LIMIT 1
            """,
            (target.id, start, end)
        ).fetchone()

    today_rank = None

    if today_row:
        with db() as con:
            ranking_today = con.execute("""
                SELECT user_id, MAX(value) AS score
                FROM rolls
                WHERE actor_type = 'user'
                  AND rolled_at BETWEEN ? AND ?
                GROUP BY user_id
                ORDER BY score DESC
            """, (start, end)).fetchall()

        for index, (uid, _) in enumerate(ranking_today, start=1):
            if uid == target.id:
                today_rank = index
                break

    if stats["rolls"] == 0:
        await interaction.followup.send(
            "No statistics exists for this user.",
            ephemeral=True
        )
        return

    if is_bot_stats:

        embed = discord.Embed(
            title=f"{BOT_NAME} Statistics",
            color=discord.Color.dark_gray()
        )

        embed.set_thumbnail(
            url=bot.user.display_avatar.url
        )

        started = "Unknown"

        if stats["first_roll"]:
            started = datetime.fromtimestamp(
                stats["first_roll"],
                TZ
            ).strftime("%b %d, %Y")

        if stats["today_roll"] is not None:
            today_value = f"Roll: {stats['today_roll']}"
        else:
            today_value = "No roll yet."

        embed.add_field(
            name="Today",
            value=today_value,
            inline=True
        )

        best_text = str(stats["best"])

        if stats["best"] == 100 and stats["perfects"] > 0:
            best_text = f"100 (×{stats['perfects']})"

        embed.add_field(
            name="Best roll",
            value=best_text,
            inline=True
        )

        embed.add_field(
            name="Worst roll",
            value=str(stats["worst"]),
            inline=True
        )

        embed.add_field(
            name="Averages",
            value=(
                f"All-time: {stats['avg']:.1f}\n"
                f"Last 10: {stats['avg10']:.1f}"
            ),
            inline=True
        )

        embed.add_field(
            name="All-time",
            value=(
                f"Rolls: {stats['rolls']:,}\n"
                f"Score: {stats['score']:,}"
            ),
            inline=True
        )

        embed.add_field(
            name="Playing since",
            value=started,
            inline=True
        )

        await interaction.followup.send(
            embed=embed
        )

        return

    embed = discord.Embed(
        title=f"{target.name} Statistics",
        color=discord.Color.dark_gray()
    )

    embed.set_thumbnail(url=target.display_avatar.url)

    if today_row:
        today_value = (
            f"Roll: {today_row[0]}\n"
            f"Rank: #{today_rank}"
        )
    else:
        today_value = (
            "Roll: —\n"
            "Rank: —"
        )

    embed.add_field(
        name="Today",
        value=today_value,
        inline=True
    )

    embed.add_field(
        name="Streaks",
        value=(
            f"Current: {current_streak}d\n"
            f"Best: {best_streak}d"
        ),
        inline=True
    )

    # Row 2
    embed.add_field(
        name="Averages",
        value=(
            f"All-time: {stats['avg']:.1f}\n"
            f"Last 10: {stats['avg10']:.1f}"
        ),
        inline=True
    )

    week_rank = (
        f"#{week_stats['rank']}"
        if week_stats["rank"] is not None
        else "—"
    )

    week_best = (
        str(week_stats["best"])
        if week_stats["rolls"] > 0
        else "—"
    )

    embed.add_field(
        name="This Week",
        value=(
            f"Rolls: {week_stats['rolls']}\n"
            f"Score: {week_stats['score']:,}\n"
            f"Rank: {week_rank}\n"
            f"Best: {week_best}"
        ),
        inline=True
    )

    month_rank = (
        f"#{month_stats['rank']}"
        if month_stats["rank"] is not None
        else "—"
    )

    month_best = (
        str(month_stats["best"])
        if month_stats["rolls"] > 0
        else "—"
    )

    embed.add_field(
        name="This Month",
        value=(
            f"Rolls: {month_stats['rolls']}\n"
            f"Score: {month_stats['score']:,}\n"
            f"Rank: {month_rank}\n"
            f"Best: {month_best}"
        ),
        inline=True
    )

    best_text = str(stats["best"])

    if stats["best"] == 100 and stats["perfects"] > 0:
        best_text = f"100 (×{stats['perfects']})"

    embed.add_field(
        name="All-time",
        value=(
            f"Rolls: {stats['rolls']}\n"
            f"Score: {stats['score']:,}\n"
            f"Rank: #{stats['rank']}\n"
            f"Best: {best_text}"
        ),
        inline=True
    )

    playing_since = "Unknown"

    if stats["first_roll"]:
        playing_since = datetime.fromtimestamp(
            stats["first_roll"],
            TZ
        ).strftime("%b %d, %Y")

    embed.add_field(
        name="Playing Since",
        value=playing_since,
        inline=True
    )

    await interaction.followup.send(embed=embed)

@bot.tree.command(
    name="help",
    description="Help and setup instructions"
)
async def help_cmd(interaction: discord.Interaction):

    log_command(interaction, "/help")

    embed = discord.Embed(
        title=BOT_NAME,
        description=(
            "**Daily Roll**\n"
            "Use the daily **🎲 Roll** button or `/roll` once per day. "
            "Rolls reset at midnight UK time.\n\n"
            "**Commands**\n"
            "• `/roll` — Roll your daily number\n"
            "• `/leaderboards` — View Hyped rankings\n"
            "• `/stats` — View detailed statistics\n\n"
            "The current daily leader automatically receives the "
            "**🎲 Highest Daily Roller** role. A tied roll does not take the crown."
        ),
        color=discord.Color.dark_grey()
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )

async def perform_roll(interaction: discord.Interaction):
    start, end = today_range()

    with db() as con:
        row = con.execute(
            """
            SELECT value
            FROM rolls
            WHERE user_id = ?
              AND actor_type = 'user'
              AND rolled_at BETWEEN ? AND ?
            LIMIT 1
            """,
            (interaction.user.id, start, end)
        ).fetchone()

    if row:
        value = row[0]
        time_left = get_time_left()

        await interaction.response.send_message(
            f"{interaction.user.mention}\n"
            f"You already rolled **{value}** today.\n"
            f"Try again in {time_left}."
        )
        return

    value = secrets.randbelow(100) + 1

    now = datetime.now(TZ)

    april_fools = (
        now.month == 4 and
        now.day == 1
    )

    if april_fools:
        steps = 10 + secrets.randbelow(11)
        print("--- APRIL FOOLS ACTIVE ---")
    else:
        steps = secrets.randbelow(6)

    if steps == 0:
        upsert_user(interaction.user.id, interaction.user.name)
        success = insert_roll(interaction.user.id, interaction.user.name, value, "user")

        if not success:
            with db() as con:
                row = con.execute(
                    """
                    SELECT value
                    FROM rolls
                    WHERE user_id = ?
                      AND actor_type = 'user'
                      AND rolled_at BETWEEN ? AND ?
                    LIMIT 1
                    """,
                    (interaction.user.id, start, end)
                ).fetchone()

            if row:
                existing_value = row[0]
                time_left = get_time_left()

                await interaction.response.send_message(
                    f"{interaction.user.mention}\n"
                    f"You already rolled **{existing_value}** today.\n"
                    f"Try again in {time_left}."
                )
            return

        if value == 100:
            await interaction.response.send_message(
                f"{interaction.user.mention} rolled 💯 🎲"
            )
        else:
            await interaction.response.send_message(
                f"{interaction.user.mention} rolled **{value}** 🎲"
            )

        await sync_high_roller_role()
        return

    await interaction.response.send_message(
        f"{interaction.user.mention} rolling..."
    )
    msg = await interaction.original_response()

    for _ in range(steps):

        if april_fools:                                             # April Fools' Day event
            fake = secrets.randbelow(900_000_000) + 100_000_000     # Displays fake massive roll values on April 1st.
        else:                                                       # Real values are still stored in the database.
            fake = secrets.randbelow(100) + 1

            while fake == value:
                fake = secrets.randbelow(100) + 1

        await msg.edit(
            content=f"{interaction.user.mention} rolling {fake:,}"
        )

        if april_fools:
            await asyncio.sleep(0.2)
        else:
            await asyncio.sleep(0.5)

    upsert_user(interaction.user.id, interaction.user.name)
    success = insert_roll(interaction.user.id, interaction.user.name, value, "user")

    if not success:
        with db() as con:
            row = con.execute(
                """
                SELECT value
                FROM rolls
                WHERE user_id = ?
                  AND actor_type = 'user'
                  AND rolled_at BETWEEN ? AND ?
                LIMIT 1
                """,
                (interaction.user.id, start, end)
            ).fetchone()

        if row:
            existing_value = row[0]
            time_left = get_time_left()

            await msg.edit(
                content=(
                    f"{interaction.user.mention}\n"
                    f"You already rolled **{existing_value}** today.\n"
                    f"Try again in {time_left}."
                )
            )
        return

    current_streak, _ = calculate_streaks(interaction.user.id)
    milestones = {10, 25, 50, 100, 250, 500, 1000}

    display_value = value

    if april_fools:
        display_value = (
            value * 10_000_000
            + secrets.randbelow(90_000_000)
        )

    if current_streak in milestones:
        await msg.edit(
            content=(
                f"{interaction.user.mention} rolled **{display_value:,}** 🎲\n"
                f"🔥 {current_streak}-day streak achieved."
            )
        )
    else:
        if value == 100:
            await msg.edit(
                content=f"{interaction.user.mention} rolled 💯 🎲"
            )
        else:
            await msg.edit(
                content=f"{interaction.user.mention} rolled **{display_value:,}** 🎲"
            )

    await sync_high_roller_role()


@bot.tree.command(
    name="roll",
    description="Roll a number between 1 and 100 (once per day)"
)
async def roll(interaction: discord.Interaction):
    log_command(interaction, "/roll")

    if interaction.channel_id != ROLL_CHANNEL_ID:
        await interaction.response.send_message(
            f"Use `/roll` in <#{ROLL_CHANNEL_ID}> 🎲",
            ephemeral=True
        )
        return

    await perform_roll(interaction)

@bot.tree.command(
    name="leaderboards",
    description="View leaderboards for different periods"
)
@app_commands.describe(period="Select leaderboard period")
@app_commands.choices(period=[
    app_commands.Choice(name="Today", value="today"),
    app_commands.Choice(name="Week", value="week"),
    app_commands.Choice(name="Month", value="month"),
    app_commands.Choice(name="Year", value="year"),
    app_commands.Choice(name="All Time", value="alltime"),
    app_commands.Choice(name="Streak", value="streak"),
    app_commands.Choice(name="100 Rolls", value="100s"),
])
async def leaderboards(
    interaction: discord.Interaction,
    period: app_commands.Choice[str] = None
):
    log_command(interaction, "/leaderboards")

    period_value = period.value if period else "today"

    now = datetime.now(TZ)

    rows = []
    ranking = []
    stats_row = (0, 0)

    # =========================
    # 100 ROLLS LEADERBOARD
    # =========================
    if period_value == "100s":

        with db() as con:
            rows = con.execute("""
                SELECT
                    COALESCE(u.username, 'Unknown'),
                    COUNT(*) AS perfects,
                    r.user_id
                FROM rolls r
                LEFT JOIN users u ON u.user_id = r.user_id
                WHERE r.actor_type = 'user'
                  AND r.value = 100
                GROUP BY r.user_id
                ORDER BY perfects DESC
                LIMIT 10
            """).fetchall()

            ranking = con.execute("""
                SELECT
                    user_id,
                    COUNT(*) AS perfects
                FROM rolls
                WHERE actor_type = 'user'
                  AND value = 100
                GROUP BY user_id
                ORDER BY perfects DESC
                LIMIT 100
            """).fetchall()

            stats_row = con.execute("""
                SELECT
                    COUNT(DISTINCT user_id),
                    COUNT(*)
                FROM rolls
                WHERE actor_type = 'user'
                  AND value = 100
            """).fetchone()

        title_suffix = "100 Rolls"

    # =========================
    # STREAK LEADERBOARD
    # =========================
    elif period_value == "streak":

        with db() as con:
            all_dates = con.execute("""
                SELECT r.user_id, r.roll_date, COALESCE(u.username, r.username) as username
                FROM rolls r
                LEFT JOIN users u ON u.user_id = r.user_id
                WHERE r.actor_type = 'user' AND r.roll_date IS NOT NULL
                GROUP BY r.user_id, r.roll_date
                ORDER BY r.user_id, r.roll_date
            """).fetchall()

        user_dates = {}
        user_names = {}

        for uid, roll_date, username in all_dates:
            if uid not in user_dates:
                user_dates[uid] = set()
                user_names[uid] = username
            user_dates[uid].add(roll_date)

        # Beräkna best streak per användare
        streak_data = []

        for uid, date_set in user_dates.items():
            dates = sorted(datetime.fromisoformat(d).date() for d in date_set)
            best = 1
            current_run = 1

            for i in range(1, len(dates)):
                if dates[i] == dates[i - 1] + timedelta(days=1):
                    current_run += 1
                    best = max(best, current_run)
                else:
                    current_run = 1

            if best >= 2:
                streak_data.append((uid, best, user_names[uid]))

        streak_data.sort(key=lambda x: x[1], reverse=True)

        for uid, best, username in streak_data:
            rows.append((username, best, uid))
            ranking.append((uid, best))

        rows = rows[:10]

        title_suffix = "Longest Streaks"
        stats_row = (len(streak_data), None)

    # =========================
    # NORMAL PERIOD LEADERBOARDS
    # =========================
    else:

        start_ts = None
        end_ts = None

        if period_value == "today":
            start = datetime(now.year, now.month, now.day, tzinfo=TZ)
            end = start + timedelta(days=1)

            start_ts = int(start.timestamp())
            end_ts = int(end.timestamp())

            start_date = start.date().isoformat()
            end_date = end.date().isoformat()

            title_suffix = f"Today — {now.strftime('%B %d')}"

        elif period_value == "week":
            start = now - timedelta(days=now.weekday())
            start = datetime(start.year, start.month, start.day, tzinfo=TZ)
            end = start + timedelta(days=7)

            start_ts = int(start.timestamp())
            end_ts = int(end.timestamp())

            start_date = start.date().isoformat()
            end_date = end.date().isoformat()

            title_suffix = f"Week {now.isocalendar().week}"

        elif period_value == "month":
            start = datetime(now.year, now.month, 1, tzinfo=TZ)

            if now.month == 12:
                end = datetime(now.year + 1, 1, 1, tzinfo=TZ)
            else:
                end = datetime(now.year, now.month + 1, 1, tzinfo=TZ)

            start_ts = int(start.timestamp())
            end_ts = int(end.timestamp())

            start_date = start.date().isoformat()
            end_date = end.date().isoformat()

            title_suffix = now.strftime("%B")

        elif period_value == "year":
            start = datetime(now.year, 1, 1, tzinfo=TZ)
            end = datetime(now.year + 1, 1, 1, tzinfo=TZ)

            start_ts = int(start.timestamp())
            end_ts = int(end.timestamp())

            start_date = start.date().isoformat()
            end_date = end.date().isoformat()

            title_suffix = f"{now.year}"

        elif period_value == "alltime":
            title_suffix = "All Time"

        with db() as con:

            if period_value == "alltime":

                rows = con.execute("""
                    SELECT
                        COALESCE(u.username, 'Unknown'),
                        s.score,
                        s.user_id
                    FROM user_scores s
                    LEFT JOIN users u ON u.user_id = s.user_id
                    ORDER BY s.score DESC
                    LIMIT 10
                """).fetchall()

                ranking = con.execute("""
                    SELECT user_id, score
                    FROM user_scores
                    ORDER BY score DESC
                    LIMIT 100
                """).fetchall()

                stats_row = con.execute("""
                    SELECT COUNT(DISTINCT user_id), COUNT(*)
                    FROM rolls
                    WHERE actor_type = 'user'
                """).fetchone()

            else:

                rows = con.execute("""
                    SELECT COALESCE(u.username, 'Unknown'), SUM(d.score), d.user_id
                    FROM daily_scores d
                    LEFT JOIN users u ON u.user_id = d.user_id
                    WHERE d.roll_date BETWEEN ? AND ?
                    GROUP BY d.user_id
                    ORDER BY SUM(d.score) DESC
                    LIMIT 10
                """, (start_date, end_date)).fetchall()

                ranking = con.execute("""
                    SELECT d.user_id,
                           COALESCE(u.username, 'Unknown') AS username,
                           SUM(d.score) AS score
                    FROM daily_scores d
                    LEFT JOIN users u ON u.user_id = d.user_id
                    WHERE d.roll_date BETWEEN ? AND ?
                    GROUP BY d.user_id
                    ORDER BY score DESC
                    LIMIT 100
                """, (start_date, end_date)).fetchall()

                stats_row = con.execute("""
                    SELECT COUNT(DISTINCT user_id), COUNT(*)
                    FROM rolls
                    WHERE rolled_at BETWEEN ? AND ?
                      AND actor_type = 'user'
                """, (start_ts, end_ts)).fetchone()

    # =========================
    # RENDER (COMMON)
    # =========================

    while len(rows) < 10:
        rows.append(("—", "—", 0))

    players_count = stats_row[0] or 0
    rolls_count = stats_row[1]

    score_label = "SCORE"

    if period_value == "streak":
        score_label = "DAYS"
    elif period_value == "100s":
        score_label = "100s"

    # =========================
    # RESET TIMER
    # =========================

    reset_text = None

    if period_value in ("today", "week", "month", "year"):
        if period_value == "today":
            reset_point = datetime(now.year, now.month, now.day, tzinfo=TZ) + timedelta(days=1)

        elif period_value == "week":
            start = now - timedelta(days=now.weekday())
            start = datetime(start.year, start.month, start.day, tzinfo=TZ)
            reset_point = start + timedelta(days=7)

        elif period_value == "month":
            if now.month == 12:
                reset_point = datetime(now.year + 1, 1, 1, tzinfo=TZ)
            else:
                reset_point = datetime(now.year, now.month + 1, 1, tzinfo=TZ)

        elif period_value == "year":
            reset_point = datetime(
                now.year + 1,
                1,
                1,
                tzinfo=TZ
            )

        remaining = max(0, int((reset_point - now).total_seconds()))

        if remaining < 3600:
            minutes = -(-remaining // 60)
            time_left = f"{minutes}m"

        elif remaining < 86400:
            hours = remaining // 3600
            time_left = f"{hours}h"

        else:

            if period_value == "year":
                weeks = remaining // (86400 * 7)
                time_left = f"{weeks}w"

            else:
                days = remaining // 86400
                time_left = f"{days}d"

        reset_text = f"Next reset in {time_left} • UK time"

    elif period_value == "alltime":

        with db() as con:
            first_roll = con.execute("""
                SELECT MIN(rolled_at)
                FROM rolls
                WHERE actor_type = 'user'
                """).fetchone()[0]

        if first_roll:
            started = datetime.fromtimestamp(first_roll, TZ)
            reset_text = f"Since {started.strftime('%b %d, %Y')}"

    png = render_leaderboard_png(
        title_suffix,
        rows,
        players_count,
        rolls_count,
        reset_text=reset_text,
        score_label=score_label
    )

    file = discord.File(
        png,
        filename="leaderboard.png"
    )

    embed = discord.Embed(
       color=discord.Color.dark_grey()
    )

    embed.set_image(
        url="attachment://leaderboard.png"
    )

    await interaction.response.send_message(
        embed=embed,
        file=file
    )

@bot.tree.command(
    name="setchannel",
    description="Set the channel for daily automatic rolls"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):

    log_command(interaction, "/setchannel")

    with db() as con:
        con.execute(
            """INSERT INTO guild_channels (guild_id, channel_id, set_at)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id)
               DO UPDATE SET channel_id=excluded.channel_id, set_at=excluded.set_at""",
            (interaction.guild.id, channel.id, int(time.time()))
        )

    await interaction.response.send_message(
        f"Daily rolls will be posted in {channel.mention}",
        ephemeral=True
    )

# ---------------- ADMIN COMMANDS ----------------

if ADMIN_MODE:

    @bot.tree.command(
        name="postroll",
        description="Owner-only post of the daily roll panel",
        guild=discord.Object(id=ADMIN_GUILD_ID)
    )
    async def postroll_admin(interaction: discord.Interaction):

        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No.", ephemeral=True)
            return

        await interaction.response.defer()

        # Treat a manual post as a manual daily reset. Passing the owner's ID
        # also clears a stale role left over from the pre-Fly/local database.
        await clear_high_roller_role(extra_user_id=interaction.user.id)

        await interaction.followup.send(
            embed=build_daily_reset_embed(),
            view=DailyRollView()
        )

        log_event(f"MANUAL DAILY RESET user={interaction.user.id}")

    @bot.tree.command(
        name="postroles",
        description="Owner-only post of the Forever role panel",
        guild=discord.Object(id=ADMIN_GUILD_ID)
    )
    async def postroles_admin(interaction: discord.Interaction):

        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No.", ephemeral=True)
            return

        if interaction.guild is None or interaction.guild.id != HYPED_GUILD_ID:
            await interaction.response.send_message(
                "Role panel is only available in Hyped.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=build_forever_role_embed(),
            view=ForeverRoleView()
        )

        log_event(f"FOREVER ROLE PANEL POSTED user={interaction.user.id} channel={interaction.channel_id}")

    @bot.tree.command(
        name="flamepreview",
        description="Owner-only preview of the Flameboard embed",
        guild=discord.Object(id=ADMIN_GUILD_ID)
    )
    async def flamepreview_admin(interaction: discord.Interaction):

        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No.", ephemeral=True)
            return

        channel_name = getattr(interaction.channel, "name", "channel")
        jump_url = (
            interaction.channel.jump_url
            if hasattr(interaction.channel, "jump_url")
            else "https://discord.com"
        )

        embed = discord.Embed(
            description=(
                "This is a preview of how a normal message will look once it "
                f"reaches {FLAMEBOARD_THRESHOLD} qualifying reactions.\n\n"
                f"#{channel_name} • [Jump to message]({jump_url})"
            ),
            color=0xD35400,
            timestamp=datetime.now(TZ)
        )

        embed.set_author(name=interaction.user.display_name)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text="🔥 1 • 😂 1 • ⭐ 1")

        await interaction.response.defer()

        try:
            cast_file = await get_flameboard_cast_file()
            await interaction.followup.send(
                embed=embed,
                file=cast_file
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, discord.HTTPException) as exc:
            log_error(f"FLAMEBOARD PREVIEW FAILED: {exc}")
            await interaction.followup.send(
                "Flameboard preview failed to load the casting image.",
                ephemeral=True
            )

        log_event(
            f"FLAMEBOARD PREVIEW user={interaction.user.id} channel={interaction.channel_id}"
        )

    @bot.tree.command(
        name="flamepreviewmedia",
        description="Owner-only Flameboard preview using an uploaded image, GIF or video",
        guild=discord.Object(id=ADMIN_GUILD_ID)
    )
    async def flamepreviewmedia_admin(
        interaction: discord.Interaction,
        media: discord.Attachment
    ):

        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No.", ephemeral=True)
            return

        content_type = media.content_type or ""

        if not (
            content_type.startswith("image/")
            or content_type.startswith("video/")
        ):
            await interaction.response.send_message(
                "Upload an image, GIF or video file.",
                ephemeral=True
            )
            return

        channel_name = getattr(interaction.channel, "name", "channel")

        embed = discord.Embed(
            description=f"#{channel_name} • Preview",
            color=0xD35400,
            timestamp=datetime.now(TZ)
        )

        embed.set_author(name=interaction.user.display_name)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text="🔥 1 • 😂 1 • ⭐ 1")

        if content_type.startswith("image/"):
            embed.set_image(url=media.url)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.defer()

            try:
                video_file = await media.to_file()
                await interaction.followup.send(
                    embed=embed,
                    file=video_file
                )
            except (discord.HTTPException, discord.NotFound):
                await interaction.followup.send(
                    content=media.url,
                    embed=embed
                )

        log_event(
            f"FLAMEBOARD MEDIA PREVIEW user={interaction.user.id} "
            f"channel={interaction.channel_id} type={content_type}"
        )

    @bot.tree.command(
        name="serveraudit",
        description="Owner-only export of server roles, channels and permissions",
        guild=discord.Object(id=ADMIN_GUILD_ID)
    )
    async def serveraudit_admin(interaction: discord.Interaction):

        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if guild is None or guild.id != HYPED_GUILD_ID:
            await interaction.followup.send(
                "Server audit is only available in Hyped.",
                ephemeral=True
            )
            return

        def enabled_permissions(permissions: discord.Permissions):
            return sorted(
                name
                for name, enabled in permissions
                if enabled
            )

        def serialise_overwrites(channel):
            rows = []

            for target, overwrite in channel.overwrites.items():
                allow, deny = overwrite.pair()

                if isinstance(target, discord.Role):
                    target_type = "role"
                else:
                    target_type = "member"

                rows.append({
                    "target_type": target_type,
                    "target_id": target.id,
                    "target_name": getattr(target, "name", str(target)),
                    "allow_value": allow.value,
                    "deny_value": deny.value,
                    "allow": enabled_permissions(allow),
                    "deny": enabled_permissions(deny),
                })

            return sorted(
                rows,
                key=lambda row: (
                    row["target_type"],
                    row["target_name"].lower()
                )
            )

        roles = []

        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            roles.append({
                "id": role.id,
                "name": role.name,
                "position": role.position,
                "colour": str(role.colour),
                "colour_value": role.colour.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "managed": role.managed,
                "is_default": role.is_default(),
                "permissions_value": role.permissions.value,
                "permissions": enabled_permissions(role.permissions),
            })

        channels = []

        for channel in sorted(guild.channels, key=lambda ch: ch.position):
            row = {
                "id": channel.id,
                "name": channel.name,
                "type": str(channel.type),
                "position": channel.position,
                "category_id": channel.category_id,
                "category_name": (
                    channel.category.name
                    if channel.category is not None
                    else None
                ),
                "permission_overwrites": serialise_overwrites(channel),
            }

            if not isinstance(channel, discord.CategoryChannel):
                row["permissions_synced"] = channel.permissions_synced

            if hasattr(channel, "topic"):
                row["topic"] = channel.topic

            if hasattr(channel, "nsfw"):
                row["nsfw"] = channel.nsfw

            if hasattr(channel, "slowmode_delay"):
                row["slowmode_delay"] = channel.slowmode_delay

            if hasattr(channel, "bitrate"):
                row["bitrate"] = channel.bitrate

            if hasattr(channel, "user_limit"):
                row["user_limit"] = channel.user_limit

            channels.append(row)

        audit = {
            "generated_at": datetime.now(TZ).isoformat(),
            "bot": BOT_NAME,
            "guild": {
                "id": guild.id,
                "name": guild.name,
                "member_count": guild.member_count,
                "owner_id": guild.owner_id,
                "verification_level": str(guild.verification_level),
                "default_notifications": str(guild.default_notifications),
                "explicit_content_filter": str(guild.explicit_content_filter),
                "features": list(guild.features),
                "system_channel_id": (
                    guild.system_channel.id
                    if guild.system_channel is not None
                    else None
                ),
                "rules_channel_id": (
                    guild.rules_channel.id
                    if guild.rules_channel is not None
                    else None
                ),
            },
            "notes": [
                "This is a read-only structural export.",
                "No message content is included.",
                "Role member counts are intentionally omitted because the bot does not use the privileged members intent.",
            ],
            "roles": roles,
            "channels": channels,
        }

        payload = json.dumps(
            audit,
            indent=2,
            ensure_ascii=False
        ).encode("utf-8")

        audit_file = discord.File(
            io.BytesIO(payload),
            filename=f"hyped-server-audit-{datetime.now(TZ).date().isoformat()}.json"
        )

        await interaction.followup.send(
            "Server structure export attached.",
            file=audit_file,
            ephemeral=True
        )

        log_event(f"SERVER AUDIT EXPORTED user={interaction.user.id}")

    @bot.tree.command(
        name="export",
        description="Owner-only export",
        guild=discord.Object(id=ADMIN_GUILD_ID)
    )
    async def export_admin(interaction: discord.Interaction):

        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # ========================
        # GUILD DATA
        # ========================
        guild_buffer = io.StringIO()
        guild_writer = csv.writer(guild_buffer)

        guild_writer.writerow(["guild_id", "guild_name", "member_count"])

        for g in bot.guilds:
            guild_writer.writerow([g.id, g.name, g.member_count])

        guild_file = discord.File(
            io.BytesIO(guild_buffer.getvalue().encode()),
            filename="guilds.csv"
        )

        # ========================
        # GLOBAL BOT STATS
        # ========================
        with db() as con:
            total_users, total_rolls = con.execute("""
                SELECT COUNT(DISTINCT user_id), COUNT(*)
                FROM rolls
                WHERE actor_type = 'user'
            """).fetchone()

            total_bot_rolls = con.execute("""
                SELECT COUNT(*)
                FROM rolls
                WHERE actor_type = 'bot'
            """).fetchone()[0]

        stats_buffer = io.StringIO()
        stats_writer = csv.writer(stats_buffer)

        stats_writer.writerow(["metric", "value"])
        stats_writer.writerow(["guilds", len(bot.guilds)])
        stats_writer.writerow(["users", total_users or 0])
        stats_writer.writerow(["user_rolls", total_rolls or 0])
        stats_writer.writerow(["bot_rolls", total_bot_rolls or 0])

        stats_file = discord.File(
            io.BytesIO(stats_buffer.getvalue().encode()),
            filename="bot_stats.csv"
        )

        await interaction.followup.send(
            content="Admin export:",
            files=[guild_file, stats_file],
            ephemeral=True
        )

    @bot.tree.command(
        name="user",
        description="Admin view of a user's roll data",
        guild=discord.Object(id=ADMIN_GUILD_ID)
    )
    async def admin_user(
        interaction: discord.Interaction,
        user: discord.User | None = None,
        user_id: str | None = None
    ):

        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "No.",
                ephemeral=True
            )
            return

        if user:
            uid = user.id

        elif user_id:
            try:
                uid = int(user_id)
            except ValueError:
                await interaction.response.send_message(
                    "Invalid user ID.",
                    ephemeral=True
                )
                return

        else:
            await interaction.response.send_message(
                "Provide a user or user ID.",
                ephemeral=True
            )
            return

        try:
            fetched_user = await bot.fetch_user(uid)
            username = fetched_user.name
        except Exception:
            username = f"Unknown ({uid})"

        with db() as con:

            rolls = con.execute("""
                SELECT value, rolled_at
                FROM rolls
                WHERE user_id = ?
                  AND actor_type = 'user'
                ORDER BY rolled_at
            """, (uid,)).fetchall()

        if not rolls:
            await interaction.response.send_message(
                "User has no rolls.",
                ephemeral=True
            )
            return

        values = [r[0] for r in rolls]

        total_rolls = len(values)
        best_roll = max(values)
        avg_roll = round(sum(values) / total_rolls, 2)

        current, best = calculate_streaks(uid)

        first_roll = datetime.fromtimestamp(rolls[0][1], TZ).strftime("%Y-%m-%d")
        last_roll = datetime.fromtimestamp(rolls[-1][1], TZ).strftime("%Y-%m-%d")

        await interaction.response.send_message(
            f"User: {username}\n"
            f"Rolls: {total_rolls}\n"
            f"Best roll: {best_roll}\n"
            f"Average: {avg_roll}\n"
            f"Current streak: {current}\n"
            f"Best streak: {best}\n"
            f"First roll: {first_roll}\n"
            f"Last roll: {last_roll}",
            ephemeral=True
        )

# ---------------- RUN ----------------
required_config = {
    "DISCORD_TOKEN": TOKEN,
    "HYPED_GUILD_ID": HYPED_GUILD_ID,
    "ROLL_CHANNEL_ID": ROLL_CHANNEL_ID,
    "HIGH_ROLLER_ROLE_ID": HIGH_ROLLER_ROLE_ID,
}

missing_config = [
    name for name, value in required_config.items()
    if not value
]

if missing_config:
    raise RuntimeError(
        "Missing required configuration: " + ", ".join(missing_config)
    )

bot.run(TOKEN)
