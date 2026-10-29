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
  (metadata only; not raw SimC or screenshots), RM - Raid History.
- In RM - Roster Builder, Ogre writes ONLY A:H. I:O are manual leadership
  choices and are never overwritten by the bot.
- RM - Attendance remains manual.
- Original Roster, Attendance, Assignments and other guild tabs stay untouched.

## Optional raid title and compact event layout

In private `/raid poster`, **Optional Raid Title** adds text after the
fixed `<Hyped> Raid #N` prefix, e.g. `<Hyped> Raid #1 - First Clear`.
Leave it empty for the original title; reopen the option and submit blank
to clear it. Existing title text persists when older `/raid setup` commands
edit a saved raid. It does not change the raid ID or existing sign-ups.

The Discord raid card displays the localised `<t:...:D> at <t:...:t>`
on one line with no Date/Time labels. Use one blank line, not multiple,
between the title/date, event details and roster. Until the Hyjal team is
announced it shows `🔹🔸 - Forming, await announcement`. The two Barrows
teams show `🔸 - Not announced` and `🔹 - Not announced` on their
respective lines. There are no voice-link buttons.

## Raid member controls

The live Discord raid card has exactly four buttons:

- **Sign** (green): sign up for the whole raid night. First-time users
  register character name, class, spec and role in the same flow.
  Returning users pick a saved character. An optional raid note is collected.
- **Update**: choose an owned character and change class, spec or raid role.
  The same character ID remains linked to sign-ups and saved gear.
- **Gear**: choose an owned character and upload SimC text, a .txt/.simc
  file, PNG/JPG/WEBP character-panel screenshot, or both in one private modal.
- **Absent** (red): optional note; Ogre records server-side signed-off time.

Screenshots are validated and compressed into the **raid-only SQLite database**.
Only upload timestamps and formats (not image bytes or raw SimC) are mirrored
to RM - Gear Checks. The owner can privately inspect the latest screenshot
using /raid gear-review character_id:<number>. Gear is optional for sign-ups.
Discord modal uploads require discord.py 2.7 or later.

## Partially reveal a roster

On an RM - Roster Builder row for a raid night:

- I (Selection): set Selected.
- K (Show on Discord): set Yes to reveal ONLY this player.
- L (Ony), M (Hyjal), N (Barrows): choose instance assignments.
  Hyjal remains one 20-player roster. N accepts 🔸 or 🔹 to distinguish
  the two Barrows 10-player groups. Existing Group A/B values remain
  compatible until you edit those rows.
- O (Player Source): Core or PUG.

PUGs can be entered manually in A (Raid ID), E (Character), I (Selection),
K (Show on Discord), L:N (assignments) and O (PUG), with no Discord ID.
Setting K back to No hides that player on the next successful sync.

Names remain private unless BOTH I=Selected and K=Yes. In the single
Roster section, **Hyjal shows one unsplit 20-player roster** with one
"Not announced" placeholder until names are revealed. There are no 🔸/🔹
labels or separate teams under Hyjal. Barrows (10-player) continues to show
two groups labelled 🔸 and 🔹, corresponding to the selected split in
column N. Old Group A/B values remain compatible for existing rows.
Ony (40-player) has its own roster when scheduled.

**The raid card contains no clickable voice-channel links or join buttons.**
A player selected for both Hyjal and Barrows appears in the Hyjal roster and
their assigned Barrows group. Approved players without a matching instance
allocation appear under "Selected · group pending". Unannounced groups display
a small "Not announced" placeholder.

Roster Announcement sits above the Roster section, immediately below Check,
and uses the relative Discord timestamp. Announcement timing is display-only;
it does not automatically publish the rest. The banner stays inside the
gold-bordered panel below the grouped rosters.

The current synchroniser does NOT delete mirrored historic rows on
raid deletion; any mirrored record cleanup is manual for now.

## Configuration

RAID_GOOGLE_SERVICE_ACCOUNT_B64: base64 of the downloaded JSON service-account
key. RAID_SHEETS_ID is required: copy the spreadsheet ID from the existing
<Hyped> Forever Google Sheet URL. This is set as a Fly secret, not hardcoded.
RAID_DB_PATH is the existing separate SQLite database path.
The loop remains disabled until credentials are configured.
Never commit the service account JSON or paste it into Discord or chat.
