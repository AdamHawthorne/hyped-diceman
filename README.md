# RollF 🎲

![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/github/v/release/hegernat/rollf-bot)
![Discord Bot](https://img.shields.io/badge/bot-live-blue)

[![Invite RollF](https://img.shields.io/badge/Invite-RollF-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1409207722783543347&permissions=2147568640&integration_type=0&scope=bot+applications.commands)

RollF is a minimal daily roll Discord bot built around one simple idea:

Everyone gets exactly **one roll per day** (1–100).

No rerolls.  
No economy.  
No gambling mechanics.

Just a daily number and some competition.

RollF is the official public instance operated by the original author.
The source code for the bot is available here on GitHub.

---

## Features

- One roll per user per calendar day (Europe/Stockholm)
- Seasonal easter egg event
- Roll range: 1–100
- Automatic daily bot roll
- Global cross-server leaderboards
- Period-based leaderboards:
  - Today
  - Week
  - Month
  - Year
  - All Time
  - Longest Streaks
- PNG-rendered leaderboards with integrated reset timers
- Cosmetic top.gg voter reward (random pastel username for 24 hours)
- Historical streak leaderboards
- Admin user lookup by Discord User ID
- Streak tracking (current and best)
- Streak milestone notifications
- Detailed user statistics:
  - Rank
  - Best roll
  - Averages
  - Period breakdown (week & month)
- Special display for perfect roll (💯)
- Reset timers for active periods
- Persistent SQLite storage
- Automated compressed weekly database backups (180-day retention)
- Optional rotating operational logs
- Slash command usage logging
- Owner admin tools for maintenance

---

## Commands

Public:

- `/roll` – Roll your daily number
- `/leaderboards` – View period-based rankings
- `/stats` – View detailed statistics
- `/setchannel` – Configure daily bot roll channel (admin)
- `/help` – Show setup information and support links

Owner-only (optional, requires ADMIN_MODE=true):

- `/export` – Export guild & global bot statistics
- `/user` – View full roll history summary for a user


---

### Example Roll

<p align="center">
  <img src="assets/roll-example.gif" width="500">
</p>

### Leaderboards

<p align="center">
  <img src="assets/rollf-leaderboards.png" width="500">
</p>

---

## Try RollF

RollF is also listed on:

- top.gg
  https://top.gg/bot/1409207722783543347

- Discord Bot List
  https://discordbotlist.com/bots/rollf

If you enjoy RollF, consider voting on top.gg.

Voting unlocks a randomly selected pastel-colored username on the global leaderboards for 24 hours.

The reward is cosmetic only and does not affect gameplay.

---

## Optional: top.gg Integration

RollF can optionally integrate with top.gg.

Features include:

- Automatic server count updates
- Cosmetic voting rewards
- Random pastel-colored usernames on leaderboards for 24 hours after a vote

To enable voting rewards:

1. Create a webhook in your top.gg bot settings.
2. Point the webhook to:

```
http://your-domain-or-ip:8080/topgg
```

3. Forward the webhook port in your router.
4. Allow the port through your firewall.

Without this configuration, RollF works normally. Only the cosmetic voting reward is disabled.


---

## Requirements

For RollF to function correctly, the bot needs permission to:

- Send messages
- Embed links
- Read message history
- Use slash commands

Daily bot rolls are posted in the configured channel using `/setchannel`.

---

## Docker

RollF can be run as a Docker container.

### Build and start

```bash
docker compose up -d --build
```

### Stop

```bash
docker compose down
```

### View logs

```bash
docker compose logs -f
```

The container uses:

- ```bot.db``` for persistent SQLite storage
- ```assets/``` for static assets
- ```.env``` for runtime configuration and secrets

The SQLite database and ```.env``` file are intentionally excluded from Git.

The included ```compose.yml``` exposes the optional webhook endpoint on:

```127.0.0.1:8080```

---

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Dependencies are installed via `requirements.txt` and include:

- discord.py
- aiohttp
- python-dotenv

Create a .env file in the project root:
```env
# Required
DISCORD_TOKEN=your_discord_bot_token

# Optional
ADMIN_MODE=true
ENABLE_COMMAND_LOGGING=true

OWNER_ID=your_discord_user_id
ADMIN_GUILD_ID=your_admin_guild_id

# Optional bot listing integrations
BOTLIST_TOKEN=your_discordbotlist_token
TOPGG_TOKEN=your_topgg_api_token
```

Run the bot:
```
python rollf.py
```

---

## Admin Mode (Optional)

RollF supports an optional admin mode for maintenance tasks.

Admin commands are:

- Only registered if `ADMIN_MODE=true`
- Restricted to `ADMIN_GUILD_ID`
- Usable only by `OWNER_ID`

If `ADMIN_MODE=false`, admin commands are not registered at all.

Example:

```env
ADMIN_MODE=true
OWNER_ID=your_discord_user_id
ADMIN_GUILD_ID=your_admin_guild_id
```

---

## top.gg Voting Rewards (Optional)

RollF can optionally reward users who vote on top.gg.

When enabled:

- Random pastel-colored username on the global leaderboards.
- Reward lasts until the vote expires (normally 24 hours).
- Purely cosmetic — gameplay is unaffected.

To enable this feature:

1. Create a webhook in your top.gg bot settings.
2. Point it to:

```
http://your-domain-or-ip:8080/topgg
```

3. Forward port `8080` to the machine running RollF.
4. Allow the port through your firewall.

If this integration is not configured, RollF continues to function normally. Only the cosmetic voting reward is unavailable.

---

## Production Notes

RollF is designed for long-term unattended operation.

The bot:

- Uses persistent SQLite storage
- Performs automatic schema migrations on startup
- Supports Docker deployment
- Supports optional operational logging
- Supports slash command usage logging
- Supports automated database backups

RollF performs automatic schema migrations on startup, including backfilling `roll_date` for historical roll records.

Statistics are derived directly from stored roll history.

---

## Name & Official Instance

RollF is open source under the MIT license.

However, the name **"RollF"** and the official public bot instance are maintained by the original author.

Forks and modified versions are welcome, but please do not present them as the official RollF bot.

If you run your own instance, please use a different bot name to avoid confusion with the official deployment.