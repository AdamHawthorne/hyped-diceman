# Hyped Diceman 🎲

A private daily dice-roll Discord bot for the **Hyped** community.

Each member gets one roll from **1–100 per UK calendar day**. The bot tracks statistics and leaderboards, and the current daily leader automatically holds the **👑 High Roller** role until somebody rolls higher.

## Features

- One roll per member per day
- UK reset at midnight using `Europe/London`
- Daily, weekly, monthly, yearly and all-time leaderboards
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

Hyped Diceman is a modified fork of **RollF**, originally created by **hegernat/plutonium87**:

https://github.com/hegernat/rollf-bot

The fork deliberately uses a different project/bot name from the public RollF instance.

The project remains licensed under the **MIT License**. The original copyright and permission notice are preserved unchanged in `LICENSE`, as required by the licence. See `NOTICE.md` for attribution details.

Changes specific to Hyped are maintained separately from the upstream project.
