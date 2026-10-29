"""Offline tests for the optional Google Sheets raid synchronisation bridge."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

from cogs import raid_sheets, raid_schedule, raid_signup as raid


class FakeSession:
    def __init__(self):
        self.calls = []
        self.closed = False

    def close(self):
        self.closed = True


class RaidSheetsTests(unittest.TestCase):
    def setup_db(self, path):
        raid.create_schema(path)
        raid_schedule.ensure_raid_fields(path)
        raid_schedule.save_raid(
            path, 1, "2026-12-09 21:00", "Hyjal (20m)", "Ony (40m)",
            roster_announce="2026-12-08 21:00"
        )
        raid.save_character(42, "Snore Mon", "Mage", "Frost", "DPS", path)
        raid.save_character(42, "Second Character", "Warrior", "Fury", "DPS", path)
        char_id = raid.member_characters(42, path)[0][0]
        raid.save_signup(1, 42, char_id, path)
        raid.save_absence(1, 99, "Family plans", path)

    def test_snapshot_maps_to_expected_rm_tabs_and_signoff_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_db(path)
            desired = raid_sheets._desired_rows(path)
            self.assertEqual(len(desired["characters"]), 2)
            self.assertEqual(len(desired["roster"]), 1)
            self.assertEqual(len(desired["signups"]), 2)
            absent = desired["signups"]["1:99"]
            self.assertEqual(absent[5], "Unavailable")
            self.assertEqual(absent[6], "Family plans")
            self.assertTrue(absent[9].endswith("+00:00"))
            self.assertEqual(len(desired["roster"]["1:42"]), 8)
            self.assertNotIn("Realm", json.dumps(desired))

    def test_character_discord_name_from_live_guild_and_existing_sheet_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_db(path)
            # Two characters from one member should only require one lookup.
            self.assertEqual(raid_sheets.character_discord_ids(path), [42])
            default = raid_sheets._desired_rows(path)
            first_id = next(iter(default["characters"]))
            self.assertEqual(default["characters"][first_id][2], "")
            renamed = raid_sheets._desired_rows(path, discord_names={42: "Ogre"})
            self.assertEqual(renamed["characters"][first_id][2], "Ogre")
            self.assertEqual(default["characters"][first_id][3], "Snore Mon")

            # A temporarily unavailable Discord API must not blank a known name.
            row = [*default["characters"][first_id]]
            row[2] = "Previous Name"
            sheet = [["Character ID", "Discord ID", "Discord Name"], row]
            calls = []

            def fake_request(session, method, endpoint, **kwargs):
                calls.append(kwargs)
                return {}

            with patch.object(raid_sheets, "_json", side_effect=fake_request):
                written = raid_sheets._sync_rows(
                    FakeSession(), "characters", sheet,
                    {first_id: default["characters"][first_id]}
                )
            self.assertEqual(written, 0)
            self.assertFalse(calls)

            with patch.object(raid_sheets, "_json", side_effect=fake_request):
                written = raid_sheets._sync_rows(
                    FakeSession(), "characters", sheet,
                    {first_id: renamed["characters"][first_id]}
                )
            self.assertEqual(written, 1)
            self.assertEqual(calls[-1]["json"]["data"][0]["range"],
                             "'RM - Characters'!A2:J2")
            self.assertEqual(calls[-1]["json"]["data"][0]["values"][0][2], "Ogre")

    def test_public_roster_only_reveals_explicit_selected_rows_with_pugs(self):
        header = [
            "Raid ID", "Raid Date", "Discord ID", "Character ID",
            "Character", "Class", "Spec", "Assigned Role", "Selection",
            "Bench Notes", "Show on Discord", "Ony (40m)",
            "Hyjal (20m)", "Barrows (10m)", "Player Source"
        ]
        approved = [
            "1", "2026-12-09", "42", "7", "Snore Mon", "Mage",
            "Frost", "DPS", "Selected", "", "Yes", "Selected",
            "Selected", "Group A", "Core"
        ]
        hidden = approved.copy()
        hidden[4] = "Secret Gamer"
        hidden[10] = "No"
        bench = approved.copy()
        bench[4] = "Bench Gamer"
        bench[8] = "Bench"
        pug = approved.copy()
        pug[2] = ""
        pug[3] = ""
        pug[4] = "Pug Hero"
        pug[12] = ""
        pug[13] = ""
        pug[14] = "PUG"
        revealed = raid_sheets._public_rows([header, approved, hidden, bench, pug])
        self.assertEqual(
            revealed[1],
            [
                ("Snore Mon", "Ony, Hyjal, Barrows 🔸"),
                ("Pug Hero", "Ony")
            ]
        )
        self.assertNotIn("Secret Gamer", str(revealed))
        self.assertNotIn("Bench Gamer", str(revealed))
        # A prior raid using the same number must not leak its roster.
        self.assertEqual(
            raid_sheets._public_rows(
                [header, approved],
                raid_dates={1: "2026-12-10"},
            ), {}
        )
        self.assertEqual(
            raid_sheets._public_rows(
                [header, approved, pug],
                raid_dates={1: "2026-12-09"},
                unavailable={"1:42"},
            ), {1: [("Pug Hero", "Ony")]}
        )

    def test_sheet_group_a_and_b_assignments_render_separately_without_hidden_raiders(self):
        headers = [
            "Raid ID", "Raid Date", "Discord ID", "Character ID",
            "Character", "Class", "Spec", "Assigned Role", "Selection",
            "Bench Notes", "Show on Discord", "Ony (40m)",
            "Hyjal (20m)", "Barrows (10m)", "Player Source",
        ]
        a = ["1", "2026-12-09", "42", "7", "Snore Mon", "Warrior",
             "Protection", "Tank", "Selected", "", "Yes", "",
             "Selected", "Group A", "Core"]
        b = ["1", "2026-12-09", "43", "8", "Healer Main", "Shaman",
             "Restoration", "Healer", "Selected", "", "Yes", "",
             "Selected", "Group B", "Core"]
        hidden = b.copy()
        hidden[2], hidden[3], hidden[4], hidden[10] = "44", "9", "Hidden Raider", "No"
        published = raid_sheets._public_rows([headers, a, b, hidden])
        event = {
            "instance_1": "Hyjal (20m)",
            "instance_2": "Barrows (10m)",
            "instance_3": None,
        }
        grouped = raid_schedule.roster_groups(event, published[1])
        self.assertEqual(grouped, [
            ("Hyjal · 20-player", ["Snore Mon", "Healer Main"]),
            ("Barrows · 10-player 🔸", ["Snore Mon"]),
            ("Barrows · 10-player 🔹", ["Healer Main"]),
        ])
        self.assertNotIn("Hidden Raider", str(grouped))
        # New sheet dropdown icons use the same destinations as legacy groups.
        updated = [r.copy() for r in (a, b)]
        updated[0][13] = "🔸"
        updated[1][13] = "🔹"
        self.assertEqual(
            raid_sheets._public_rows([headers, *updated])[1],
            [("Snore Mon", "Hyjal, Barrows 🔸"),
             ("Healer Main", "Hyjal, Barrows 🔹")],
        )

    def test_roster_sync_does_not_change_manual_selection_or_guest_rows(self):
        current = [
            ["Raid ID", "Raid Date", "Discord ID", "Character ID", "Character",
             "Class", "Spec", "Assigned Role", "Selection", "Bench Notes",
             "Show on Discord"],
            ["1", "old", "42", "7", "Old Name", "Mage", "Fire", "DPS",
             "Selected", "Keep manual note", "Yes"],
            ["1", "2026-12-09", "", "", "Pug Hero", "Warrior", "Fury",
             "DPS", "Selected", "", "Yes"],
        ]
        desired = {
            "1:42": ["1", "2026-12-09", "42", "7", "Snore Mon", "Mage",
                     "Frost", "DPS"]
        }
        session = FakeSession()
        calls = []

        def fake_request(session, method, endpoint, **kwargs):
            calls.append((method, endpoint, kwargs))
            return {}

        with patch.object(raid_sheets, "_json", side_effect=fake_request):
            raid_sheets._sync_rows(session, "roster", current, desired)
        self.assertEqual(len(calls), 1)
        payload = calls[0][2]["json"]["data"]
        self.assertEqual(payload[0]["range"], "'RM - Roster Builder'!A2:H2")
        self.assertEqual(len(payload[0]["values"][0]), 8)
        self.assertNotIn("Keep manual note", json.dumps(payload))
        self.assertNotIn("Pug Hero", json.dumps(payload))

    def test_new_signups_preserve_preformatted_dropdown_rows(self):
        """Appending raid records must not insert rows and shift validations."""
        for kind, values, expected_end in (
            ("roster", ["1", "2026-12-09", "42", "7",
                        "Snore Mon", "Warrior", "Protection", "Tank"], "H"),
            ("characters", ["7", "42", "Snore", "Snore Mon", "Warrior",
                            "Protection", "Tank", "Main", "", ""], "J"),
            ("signups", ["1", "2026-12-09", "42", "7", "Snore Mon",
                         "Available", "", "", "", ""], "J"),
        ):
            with self.subTest(kind=kind):
                tab = raid_sheets.SHEETS[kind][0]
                header = [f"Column {i}" for i in range(len(values))]
                calls = []

                def fake_request(session, method, endpoint, **kwargs):
                    calls.append((method, endpoint, kwargs))
                    return {}

                with patch.object(raid_sheets, "_json", side_effect=fake_request):
                    count = raid_sheets._sync_rows(
                        FakeSession(), kind, [header], {"new": values},
                    )
                self.assertEqual(count, 1)
                self.assertEqual(len(calls), 1)
                method, endpoint, options = calls[0]
                self.assertEqual(method, "POST")
                self.assertTrue(endpoint.endswith(":append"))
                self.assertIn(f"'{tab}'!A:{expected_end}", unquote(endpoint))
                self.assertEqual(options["params"]["insertDataOption"], "OVERWRITE")
                self.assertEqual(options["params"]["valueInputOption"], "RAW")
                self.assertEqual(options["json"]["values"], [values])

    def test_successful_remote_approval_updates_only_raid_public_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_db(path)
            approved = {1: [("Snore Mon", "Ony"),
                            ("Pug Hero", "Ony")]}
            changed = raid_sheets._save_public(path, approved)
            self.assertEqual(changed, {1})
            self.assertEqual(raid_schedule.read_public_roster(path, 1),
                             approved[1])
            self.assertEqual(raid_sheets._save_public(path, approved), set())
            self.assertEqual(raid_sheets._save_public(path, {}), {1})
            self.assertEqual(raid_schedule.read_public_roster(path, 1), [])
            self.assertEqual(raid.my_signup(1, 42, path)[1], "Available")

    def test_roster_publication_and_update_timestamps_follow_real_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_db(path)
            event = raid_schedule.read_raid(path, 1)
            self.assertIsNone(event.get("roster_posted_at"))
            self.assertIsNone(event.get("roster_updated_at"))
            self.assertIn("**Roster Announcement:**", raid_schedule.raid_roster_status(event))
            approved = {1: [("Snore Mon", "Hyjal")]}
            self.assertEqual(raid_sheets._save_public(path, approved), {1})
            posted = raid_schedule.read_raid(path, 1)
            self.assertTrue(posted["roster_posted_at"])
            self.assertIsNone(posted["roster_updated_at"])
            self.assertRegex(raid_schedule.raid_roster_status(posted),
                             r"\*\*Roster posted:\*\* <t:\d+:F>")
            self.assertEqual(raid_sheets._save_public(path, approved), set())
            same = raid_schedule.read_raid(path, 1)
            self.assertEqual(posted["roster_posted_at"], same["roster_posted_at"])
            self.assertIsNone(same["roster_updated_at"])
            changed = {1: [("Snore Mon", "Hyjal"), ("Pug Hero", "Hyjal")]}
            self.assertEqual(raid_sheets._save_public(path, changed), {1})
            updated = raid_schedule.read_raid(path, 1)
            self.assertEqual(updated["roster_posted_at"], posted["roster_posted_at"])
            self.assertTrue(updated["roster_updated_at"])
            self.assertRegex(raid_schedule.raid_roster_status(updated),
                             r"\*\*Update:\*\* <t:\d+:F>")
            self.assertEqual(raid_sheets._save_public(path, {}), {1})
            withdrawn = raid_schedule.read_raid(path, 1)
            self.assertIsNone(withdrawn["roster_posted_at"])
            self.assertIsNone(withdrawn["roster_updated_at"])
            self.assertIn("**Roster Announcement:**",
                          raid_schedule.raid_roster_status(withdrawn))

    def test_missing_credentials_do_not_initiate_connection(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(raid_sheets.configured())
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                raid_sheets.sync_once("not-a-db.db")
