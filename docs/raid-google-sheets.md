# Hyped Ogre: one Google Sheets tab per raid

This replaces the old six-tab Raid Management setup. It does NOT touch the
daily roll, gold coin, leaderboard or other bot SQLite tables.

## Workflow

1. In private `#ogre`, configure and post the panel with `/raid poster`.
   The bot automatically creates a private tab named e.g.
   **Raid #1 - Wed 09 Dec 2026** (and syncs every ~60 seconds).
2. Players use **Sign**, optionally upload SimC/gear with **Gear**, and can
   change character details with **Update**. One registered character per
   player per raid night.
3. In that raid's Google tab, use the leadership dropdowns to build the
   proposed teams. The public panel can show partial previews but describes
   them as **Roster: Forming, awaiting confirmation**.
4. In private `#ogre`, run `/raid confirm number:1` when the roster is
   genuinely ready. The bot takes a locked snapshot, changes its label to
   **Roster confirmed:**, and closes new sign-ups.
5. Use `/raid reopen number:1` if corrections are needed. It returns to
   Forming and reopens sign-ups; confirm again when satisfied.
6. After the raid, use
   `/raid archive number:1 wcl_url:https://www.warcraftlogs.com/reports/... notes:...`.
   The date, WCL link and notes are stored on the raid tab. The tab is hidden
   to keep the active Sheet tidy, **not deleted**. Use Google Sheets' hidden
   sheets menu to view it later.

## Single raid tab layout

The top rows contain the raid title, date, status, instances, confirmation
and archive timestamps, Warcraft Logs, review notes and Discord panel link.

Below the fixed headings, registrations appear under five sections:

- **TANKS**, grouped by class
- **HEALERS**, grouped by class
- **MELEE**, grouped by class
- **DPS** (ranged DPS), grouped by class
- **ABSENT**, retained separately for audit, not selectable for public roster

| Column | Value | Owner |
| --- | --- | --- |
| A | Discord name (guild nickname) | Ogre |
| B | Character | Ogre |
| C | Class | Ogre |
| D | Spec | Ogre |
| E | Role | Ogre |
| F | Main / Alt | Ogre |
| G | Sign-up notes (and absence note) | Ogre |
| H | Original signed-at timestamp | Ogre |
| I | Signed / Absent | Ogre |
| J | Selection: Pending / Selected / Bench / Not selected | Leadership |
| K | Show on Discord: No / Yes | Leadership |
| L | Hyjal: No / Yes | Leadership |
| M | Barrows: No / 🔸 / 🔹 | Leadership |
| N | Ony: No / Yes | Leadership |
| O | Leadership notes | Leadership |
| P-Q | Internal Discord and character IDs, hidden for readability | Ogre |

Every new player is **Signed** by default. Selection begins **Pending**;
Show on Discord begins **No**. This prevents accidental public disclosure.

For a player to appear in Hyjal, for example: choose **Selected** (J),
**Yes** (K) and **Yes** (L). For Barrows, select the relevant team symbol in
column M. Changes are noticed at the next successful ~60-second sync.
Hyjal remains one roster headed by 🔹🔸 and Barrows has two rosters
headed 🔸 and 🔹. The diamond headers stay visible after players appear.

When a player clicks **Absent**, Ogre moves the row to **ABSENT**, sets
selection to Not selected, disables public display, and removes them from
the public roster if necessary. The original sign-up time stays in column H,
and the signed-off timestamp and optional reason are recorded in column G
alongside any original sign-up note. A player signing up again is reset to
Pending/No, requiring fresh leadership approval.

When confirmed, subsequent manual changes to dropdowns are ignored for
public roster purposes until `/raid reopen`. Absences can still remove a
confirmed player; this shows as an **Update:**. Archived raids accept no
further roster edits or absences.

Leadership-entered PUG rows in the old Roster Builder migrate to the new
tab; PUGs can also be added manually, with a character, class/spec/role,
Status=Signed and the leadership dropdowns. Do not overwrite the hidden
internal ID columns for registered guild members.

## Previous RM tabs and data safety

On the first successful per-raid tab sync, Ogre hides the six old
`RM -` tabs. They are **never deleted** by the migration and are no longer
used as live roster data. Existing leadership selections, notes and PUG
rows for that raid are imported once. To inspect historical values, use
Google Sheets' **View > Hidden sheets** menu.

SQLite at `/data/raid_signup.db` remains authoritative for registration,
attendance and gear uploads. Only basic metadata, not raw SimC text or gear
screenshots, is synced to the new tab. The existing roll/coin database and
all other Sheet tabs are untouched.

For safety, run the Python regression suite before deploying. Do not merge
the development branch directly into another branch without checking the
full ancestry for unrelated commits.

## Google Sheets / Fly.io connection

The ChatGPT Google Drive connection cannot authenticate a Fly.io bot. Ogre
uses a Google service account with the Google Sheets API enabled. Share
the restricted `<Hyped> Forever` workbook with its service account email
as Editor, not a Google Cloud project-wide Editor role. Set these secrets
in Fly.io, keeping the JSON key outside Git and Discord:

- `RAID_SHEETS_ID` = the private Google workbook ID
- `RAID_GOOGLE_SERVICE_ACCOUNT_B64` = Base64 UTF-8 encoding of the JSON key

On Windows PowerShell, after validating the downloaded key:

```powershell
$path = "S:\\WoW\\Discord Bot\\hyped-ogre-47707d794b16.json"
$json = Get-Content -Raw -LiteralPath $path
$key = $json | ConvertFrom-Json
if ($key.type -ne "service_account" -or !$key.private_key) {
    throw "Invalid service account JSON"
}
$secret = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
fly secrets set -a hyped-diceman --stage "RAID_GOOGLE_SERVICE_ACCOUNT_B64=$secret"
Remove-Variable json,key,secret
```

These Fly secrets have already been configured on the live app. No need to
recreate or reapply them when deploying code changes. Run `/raid sync`
in private `#ogre` to diagnose permissions or synchronisation problems.
