# Hyped Ogre 🎲

A private daily dice-roll Discord bot for the **Hyped** community.

Each member gets one roll from **1–100 per UK calendar day**. The bot tracks statistics and gold coins, and the current daily leader automatically holds the **👑 High Roller** role until somebody rolls higher.

## Features

- One roll per member per day
- UK reset at midnight using `Europe/London`
- One all-time gold coin leaderboard via `/leaderboard`
- One gold coin for each completed UK day’s highest human roll; earliest roll wins ties
- Persistent V2 daily reset panel with yesterday’s winner, lifetime top five gold coin holders, and Roll, Leaderboard and My stats buttons
- `/stats` retains roll history and adds lifetime gold coins
- Roll statistics and streak tracking
- Automatic **👑 High Roller** role transfer
- First player to reach a tied high score keeps the crown
- Automatic crown removal at the daily reset
- Persistent SQLite storage
- Docker support
- Single-server safeguard for the Hyped Discord

## Configuration

Copy `.env.example` to `.env` and provide the required values.

The Discord bot token is a secret. Never commit `.env`, bot tokens, API keys or production database files to Git.

Required configuration:

- `DISCORD_TOKEN`
- `HYPED_GUILD_ID`
- `ROLL_CHANNEL_ID`
- `HIGH_ROLLER_ROLE_ID`

Optional owner/admin configuration is documented in `.env.example`.

The bot role must sit **above** the High Roller role in Discord's role hierarchy and needs permission to manage that role.

## Local setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python rollf.py
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.\venv\Scripts\Activate.ps1
```

## Docker

```bash
docker compose up -d --build
```

View logs with:

```bash
docker compose logs -f
```

## Upstream and licence

Hyped Ogre is a modified fork of **RollF**, originally created by **hegernat/plutonium87**:

https://github.com/hegernat/rollf-bot

The fork deliberately uses a different project/bot name from the public RollF instance.

The project remains licensed under the **MIT License**. The original copyright and permission notice are preserved unchanged in `LICENSE`, as required by the licence. See `NOTICE.md` for attribution details.

Changes specific to Hyped are maintained separately from the upstream project.


## Gold coins and daily reset

Gold coins are bragging rights only. At UK midnight, the highest human roll from
that completed day receives one coin. Ogre's own roll does not compete. Ties go
to the earliest highest roll, matching the High Roller crown. The V2 reset panel
announces yesterday's winner and their total, followed by the lifetime top five gold coin holders (or an empty state), with the supplied coin image beneath it.

The additive `roll_coin_awards` table uses `(guild_id, roll_date)` as its primary
key. On startup, all completed days in the existing Hyped roll database are
backfilled once. UK dates are calculated from timestamps, including older UTC
`roll_date` values. Existing rolls and score tables are preserved. The current
day is never awarded early. Restarting or retrying a failed Discord post cannot
award another coin for the same date. Missed days are included in totals without
posting a backlog of announcements.

- `/leaderboard`: one paginated server leaderboard by lifetime coin count.
- `/stats [user]`: existing statistics plus gold coins.
- `/previewroll`: owner-only private preview with disabled buttons; changes no data.
- `/postroll`: in the daily-roll channel, refreshes the existing daily panel (or
  restores it if deleted). Elsewhere in Hyped, posts a test panel without changing
  rolls, coins or crowns; its Roll button points users to the daily-roll channel.
- `/awardcoin day:YYYY-MM-DD user:@member winning_roll:92`: owner-only recovery
  for a completed day missing from saved history. Grants exactly one coin, records
  the owner, rejects bots/future dates and refuses to duplicate or replace a day's
  existing award. Saved rolls take priority. Use one command per missing day.

After merging the V2 feature branch into `hyped-customisation`, deploy that branch as usual. Startup creates the ledger,
backfills saved history, upgrades today's existing reset panel to V2, and syncs
the singular `/leaderboard` command (the old period-based `/leaderboards` is removed).
A tested backup should be retained when deploying database changes. No live
Discord data is accessed by the automated test suite.
