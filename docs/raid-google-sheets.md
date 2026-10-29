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

    git pull --ff-only origin feature/raid-signup-sheets-v2
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

The Discord raid card displays a plain, full UK weekday/date on one line,
then hoverable Discord start/end timestamps using `<t:...:t> - <t:...:t>`
on the next line. The times adjust for the viewer's Discord timezone.
There are no Date/Time labels, and Check sits directly above Roster.

In private `/raid poster`, you can set the UK start and end times
independently (preset dropdowns or exact HH:MM). By default, the raid
ends three elapsed hours after it starts; selecting `Default: +3h` resets
any custom end time. An end clock time earlier than the start is treated
as the following day. Invalid or excessively long durations are rejected.

The `Choose Check channel` Discord channel dropdown lets leadership
select the channel mentioned next to `Check:` on each raid card. Existing
raids continue using `#resources` unless changed. The end time and
Check channel are saved per raid in the raid-only SQLite database
using safe additive migrations; older setup commands retain both values.
Its accent matches the Leadership panel (`#8C5A3C`), but the private poster setup keeps its
existing accent. Roster headings display **Hyjal**, **Barrows** and **Ony**
without player counts. Until selected teams are announced, all groups use
`TBD`: Hyjal shows `🔹🔸 - TBD`, Barrows shows `🔸 - TBD` and
`🔹 - TBD`, and Ony shows `- TBD`. There are no voice-link buttons.

The schedule's **Roster Announcement** remains visible *below* the Roster
sections until the first nonempty, leadership-approved public roster is
actually synced from Google Sheets. That first reveal is labelled
**Roster posted:** with its real date/time. Later approved changes are
labelled **Update:** with the latest change date/time. Identical repeated
syncs do not change the timestamps; withdrawing the entire public roster
reverts to the planned announcement. No automatic publishing takes place
at the scheduled announcement time.

In private `/raid poster`, **Bottom Banner** accepts an optional direct
HTTPS image URL ending in `.png`, `.jpg`, `.jpeg`, `.webp`, or `.gif`.
Submit an empty field to revert to the original `y6bCxy.png` image.
The selected banner is saved per raid and appears below the Roster/status
inside the leadership-coloured raid container, above the four action buttons.

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

Names remain private unless BOTH I=Selected and K=Yes. Hyjal displays
one unsplit 20-player roster, with 🔹🔸 - TBD until announced. Barrows
has separate 🔸 and 🔹 rosters, both initially marked TBD; Ony has
its own roster when scheduled. A player selected for both Hyjal and
Barrows appears in both assigned sections. Approved players without
a matching instance allocation appear under "Selected · group pending".

The card has no clickable voice-channel or join links. Roster Announcement
sits below the roster sections and is display-only: it does not publish
anyone automatically. The banner remains below that status within the
leadership-coloured container.

The current synchroniser does NOT delete mirrored historic rows on
raid deletion; any mirrored record cleanup is manual for now.

## Configuration

RAID_GOOGLE_SERVICE_ACCOUNT_B64: base64 of the downloaded JSON service-account
key. RAID_SHEETS_ID is required: copy the spreadsheet ID from the existing
<Hyped> Forever Google Sheet URL. This is set as a Fly secret, not hardcoded.
RAID_DB_PATH is the existing separate SQLite database path.
The loop remains disabled until credentials are configured.
Never commit the service account JSON or paste it into Discord or chat.
