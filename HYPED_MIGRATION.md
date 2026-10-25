# Hyped Ogre migration

Hyped Ogre is a guild-only fork of RollF. This branch begins a conservative migration:
the dice game continues to run from `rollf.py` unchanged, and new guild features
are loaded as self-contained `discord.py` Cogs.

## Preview testing

1. Deploy this branch to a **test instance** of the existing bot, not alongside
   the production instance using the same bot token (two concurrent sessions can
   conflict, and the dice scheduler may run twice).
2. Set `HYPED_GUILD_ID` in the environment to the numeric Discord server ID.
3. Allow the bot to sync the new application command on startup. Global slash
   command propagation can take time.
4. As a server manager, run `/lfg-preview` in your test channel.
5. Pick a level bracket, then a sample character, and click Interested / Commit.
   These responses are **ephemeral demonstrations**: no data is stored, no
   parties are created and no roster is changed.

The first preview message is posted in the channel when the command is run.
Access is restricted by Discord's Manage Server permission and, if supplied,
by `HYPED_GUILD_ID`. The dropdown views expire after a day; rerun the
command to refresh.

## Architecture target

- `bot.py`: future minimal entry point and Cog loader.
- `cogs/dice/`: move the existing dice handlers and scheduling here after
  a parity test suite and a database backup.
- `cogs/lfg/`: daily signup storage, board renderer, group interest and
  commitment workflows. The first read-only mock is `cogs/lfg_preview.py`.
- `cogs/ogre/`, `cogs/raids/`, `cogs/news/`, `cogs/games/`:
  additional independent features, introduced as needed.
- `shared/`: server roster, config and database utilities.

Use separately named SQLite tables or databases for each module, even if they
share one connection provider. Keep the existing `bot.db` volume stable during
the migration. For production add per-feature error handling, persistent view
registration after restarts, retry-safe schedulers and guild-ID validation
on every mutation.

## Rename

The GitHub connector used for this change cannot rename a repository.
Rename it manually at **Repository > Settings > General > Repository name**
from `hyped-diceman` to `hyped-ogre`. Update the deployment remote and
image/service labels once the new name is set.

Renaming the Discord application is a separate change in the Discord
Developer Portal. Keep the existing application/token and server installation.
Preserve the upstream RollF attribution and MIT licence.

## Scope of this PR

Only the new LFG preview and its loader are added. The actual dice module
extraction, daily LFG scheduler, roster integration and production database
schema are follow-up PRs. This staged approach reduces risk to the existing
dice functionality.
