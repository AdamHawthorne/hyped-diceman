# Connecting Hyped Ogre to Google Sheets

The ChatGPT Drive connector does not authorise the Fly.io bot. The bot needs
a separate Google service account. Keep its JSON key private and outside Git.

## Google Cloud setup

1. In Google Cloud Console, enable Google Sheets API for a project.
2. Create a service account (IAM & Admin > Service Accounts), then create and
   download a JSON key for that service account. No project-wide Editor role needed.
3. Open the existing <Hyped> Forever spreadsheet, click Share and give the JSON
   key's client_email address Editor access to that spreadsheet.
4. In PowerShell, use the actual download location of the JSON key:

    $path = "$HOME\Downloads\hyped-ogre-sheets.json"
    $json = Get-Content -Raw $path
    $secret = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
    fly secrets set -a hyped-diceman "RAID_GOOGLE_SERVICE_ACCOUNT_B64=$secret"
    fly secrets set -a hyped-diceman "RAID_SHEETS_ID=<paste spreadsheet ID from its URL>"
    Remove-Variable json, secret

5. Deploy updated code and dependencies:

    git pull --ff-only origin feature/daily-gold-coins-v2
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m unittest discover -s tests -v
    fly deploy -a hyped-diceman

6. Run /raid sync in private #ogre. The bot also polls approximately every
   60 seconds while running. An API or permission error won't discard sign-ups.

## Sheet ownership

- Ogre SQLite writes: RM - Characters, RM - Sign-ups, RM - Gear Checks
  (metadata only; not raw SimC), RM - Raid History.
- In RM - Roster Builder, Ogre writes ONLY A:H. I:O are manual leadership
  choices and are never overwritten by the bot.
- RM - Attendance remains manual.
- Original Roster, Attendance, Assignments and other guild tabs stay untouched.

## Partially reveal a roster

On an RM - Roster Builder row for a raid night:

- I (Selection): set Selected.
- K (Show on Discord): set Yes to reveal ONLY this player.
- L (Ony), M (Hyjal), N (Barrows): set optional assignments to display
  next to that player's name. Barrows accepts Group A or Group B.
- O (Player Source): Core or PUG.

PUGs can be entered manually in A (Raid ID), E (Character), I (Selection),
K (Show on Discord), L:N (assignments) and O (PUG), with no Discord ID.
Setting K back to No hides that player on the next successful sync.

Names remain private unless BOTH I=Selected and K=Yes. Revealed names
appear under one Roster section, and Roster Announcement remains at the bottom.
Announcement timing is display-only; it does not auto-publish the rest.

The current synchroniser does NOT delete mirrored historic rows on
raid deletion; any mirrored record cleanup is manual for now.

## Configuration

RAID_GOOGLE_SERVICE_ACCOUNT_B64: base64 of the downloaded JSON service-account
key. RAID_SHEETS_ID is required: copy the spreadsheet ID from the existing
<Hyped> Forever Google Sheet URL. This is set as a Fly secret, not hardcoded.
The database path is
RAID_DB_PATH (the existing separate SQLite database).
The loop remains disabled until credentials are configured.
Never commit the service account JSON or paste it into Discord or chat.
