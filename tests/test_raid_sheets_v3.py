"""Offline tests for one-tab-per-raid roster management."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs import raid_sheets_v3 as sheet, raid_schedule, raid_signup as raid


class RaidTabsTests(unittest.TestCase):
    def setup_raid(self, path):
        raid.create_schema(path)
        raid_schedule.ensure_raid_fields(path)
        event = raid_schedule.save_raid(
            path, 1, "2026-12-09 21:00", "Hyjal (20m)", "Barrows (10m)"
        )
        raid.save_character(42, "Snore Mon", "Warrior", "Protection", "Tank", path)
        tank = raid.member_characters(42, path)[0][0]
        raid.save_signup(1, 42, tank, path, note="Main tank")
        raid.save_character(43, "Fast Rogue", "Rogue", "Combat", "DPS", path)
        rogue = raid.member_characters(43, path)[0][0]
        raid.save_signup(1, 43, rogue, path)
        raid.save_character(44, "Mage Player", "Mage", "Frost", "DPS", path)
        mage = raid.member_characters(44, path)[0][0]
        raid.save_signup(1, 44, mage, path)
        return event

    def test_tab_name_and_sorted_role_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.setup_raid(path)
            self.assertEqual(sheet.tab_title(event), "Raid #1 - Wed 09 Dec 2026")
            body = sheet._rows(path, event, {42: "Snore", 43: "Rogue", 44: "Mage"})
            self.assertEqual([r[0] for r in body if r[0] in sheet.SECTIONS],
                             list(sheet.SECTIONS))
            by_character = {row[1]: row for row in body if row[16]}
            self.assertEqual(by_character["Snore Mon"][0], "Snore")
            self.assertEqual(by_character["Snore Mon"][8], "Signed")
            self.assertEqual(by_character["Snore Mon"][9:14],
                             ["Pending", "No", "No", "No", "No"])
            self.assertEqual(by_character["Snore Mon"][6], "Main tank")
            self.assertEqual(by_character["Snore Mon"][15], "Not submitted")
            self.assertTrue(by_character["Snore Mon"][7])
            self.assertTrue(by_character["Snore Mon"][16].isdigit())
            self.assertEqual(sheet._group(
                {"role": "DPS", "class_name": "Rogue", "spec": "Combat"}, False
            ), "MELEE")
            self.assertEqual(sheet._group(
                {"role": "DPS", "class_name": "Mage", "spec": "Frost"}, False
            ), "DPS")
            header = sheet._header(event, body, sheet.tab_title(event))
            self.assertEqual(header[6][:9], list(sheet.COLUMNS[:9]))
            self.assertEqual(header[1][3], "Forming")

    def test_manual_selections_follow_id_after_sort_and_absence_moves_to_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.setup_raid(path)
            first = sheet._header(event, sheet._rows(path, event, {}), sheet.tab_title(event))
            old = [list(row) for row in first]
            tank = next(row for row in old if row[16] == "42")
            tank[9:14] = ["Selected", "Yes", "Yes", "🔸", "No"]
            again = sheet._rows(path, event, {42: "Snore"}, old_rows=old)
            selected = next(row for row in again if row[16] == "42")
            self.assertEqual(selected[9:14], ["Selected", "Yes", "Yes", "🔸", "No"])
            published = sheet._approved(sheet._header(event, again, sheet.tab_title(event)), event)
            self.assertIn(("42", "Snore Mon", "Hyjal, Barrows 🔸"), published)
            raid.save_absence(1, 42, "Unavailable", path)
            absent = sheet._rows(path, event, {}, old_rows=old)
            row = next(row for row in absent if row[16] == "42")
            self.assertEqual(row[8], "Absent")
            self.assertIn("Signed off:", row[6])
            self.assertEqual(row[9:14], ["Not selected", "No", "No", "No", "No"])
            self.assertNotIn("Snore Mon", [r[1] for r in sheet._header(
                event, absent, sheet.tab_title(event)) if r[8] == "Signed" and r[9] == "Selected"])
            self.assertNotIn("42", [x[0] for x in sheet._approved(
                sheet._header(event, absent, sheet.tab_title(event)), event
            )])
            # Signing again is not automatically reapproved for Discord.
            raid.save_signup(1, 42, raid.member_characters(42, path)[0][0], path)
            restored = sheet._rows(path, event, {}, old_rows=sheet._header(
                event, absent, sheet.tab_title(event)
            ))
            row = next(row for row in restored if row[16] == "42")
            self.assertEqual(row[8], "Signed")
            self.assertEqual(row[9:14], ["Pending", "No", "No", "No", "No"])

    def test_legacy_selections_are_migrated_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.setup_raid(path)
            legacy = {"42": {"selection": "Selected", "show": "Yes",
                             "hyjal": "Yes", "barrows": "🔹", "ony": "No",
                             "notes": "Check buffs"}}
            rows = sheet._rows(path, event, {}, legacy=legacy)
            row = next(r for r in rows if r[16] == "42")
            self.assertEqual(row[9:14], ["Selected", "Yes", "Yes", "🔹", "No"])
            self.assertEqual(row[14], "Check buffs")
            self.assertIn(("42", "Snore Mon", "Hyjal, Barrows 🔹"),
                          sheet._approved(sheet._header(event, rows, ""), event))

    def test_confirm_lock_absences_and_archive_keep_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.setup_raid(path)
            self.assertIn("**Confirmation:**", raid_schedule.raid_roster_status(event))
            with self.assertRaises(ValueError):
                raid_schedule.raid_status(path, 1, "confirm", confirmed_entries=[])
            confirmed = raid_schedule.raid_status(path, 1, "confirm",
                confirmed_entries=[("42", "Snore Mon", "Hyjal"),
                                   ("43", "Fast Rogue", "Barrows 🔸")])
            self.assertEqual(confirmed["roster_published"], 1)
            self.assertEqual(confirmed["signup_open"], 0)
            self.assertIn("Roster confirmed:", raid_schedule.raid_roster_status(confirmed))
            with self.assertRaisesRegex(ValueError, "Reopen"):
                raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            with self.assertRaisesRegex(ValueError, "aren't open"):
                raid.save_signup(1, 42, raid.member_characters(42, path)[0][0], path)
            self.assertEqual(len(raid_schedule.confirmed_roster(path, 1)), 2)
            raid.save_absence(1, 42, "Away", path)
            self.assertEqual(raid_schedule.confirmed_roster(path, 1, {"42"}),
                             [("Fast Rogue", "Barrows 🔸")])
            raid_schedule.raid_status(path, 1, "reopen")
            self.assertEqual(raid_schedule.confirmed_roster(path, 1), [])
            again = raid_schedule.raid_status(path, 1, "confirm",
                confirmed_entries=[("43", "Fast Rogue", "Barrows 🔸")])
            archived = raid_schedule.raid_status(path, 1, "archive", notes="Zero wipes",
                wcl_url="https://www.warcraftlogs.com/reports/abc123")
            self.assertEqual(archived["archive_notes"], "Zero wipes")
            self.assertEqual(archived["wcl_url"], "https://www.warcraftlogs.com/reports/abc123")
            self.assertIn("Archived:", raid_schedule.raid_roster_status(archived))
            with self.assertRaisesRegex(ValueError, "read-only"):
                raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            with self.assertRaises(ValueError):
                raid_schedule.raid_status(path, 1, "reopen")
            with self.assertRaises(ValueError):
                raid.save_absence(1, 43, "No", path)

    def test_public_roster_keeps_role_and_class_for_discord_emojis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.setup_raid(path)
            body = sheet._rows(path, event, {42: "Snore"})
            row = next(r for r in body if r[16] == "42")
            row[9:14] = ["Selected", "Yes", "Yes", "No", "No"]
            selected = sheet._approved(sheet._header(
                event, body, sheet.tab_title(event)
            ), event, include_details=True)
            self.assertIn(("42", "Snore Mon", "Hyjal", "Warrior", "Tank",
                           "Protection"), selected)
            self.assertEqual(sheet._save_public_for_one(
                path, 1, [tuple(entry[1:6]) for entry in selected]
            ), {1})
            self.assertIn(("Snore Mon", "Hyjal", "Warrior", "Tank", "Protection"),
                          raid_schedule.read_public_roster(path, 1, include_details=True))
            self.assertEqual(raid_schedule.read_public_roster(path, 1),
                             [("Snore Mon", "Hyjal")])
            # Writing same approved roster again must not produce spurious updates.
            self.assertEqual(sheet._save_public_for_one(
                path, 1, [tuple(entry[1:6]) for entry in selected]
            ), set())

    def test_class_and_role_emoji_from_fixed_guild_ids(self):
        self.assertEqual(raid_schedule.roster_name_with_emojis(
            "Snore Mon", "Warrior", "Tank", "Protection"
        ), "<:Tank:1565477012322132099> <:Warrior:1565476863835644034> Snore Mon")
        self.assertEqual(raid_schedule.roster_name_with_emojis(
            "Mage One", "Mage", "DPS", "Frost"
        ), "<:Caster:1565477140429021285> <:Mage:1565476478957916310> Mage One")
        text = raid_schedule.roster_name_with_emojis(
            "Rogue One", "Rogue", "DPS", "Combat"
        )
        self.assertEqual(
            text, "<:Melee:1565477048305197207> <:Rogue:1565476692611563550> Rogue One"
        )

    def test_public_changes_do_not_overwrite_other_raids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_raid(path)
            raid_schedule.save_raid(path, 2, "2026-12-16 21:00", "Hyjal (20m)")
            with raid_schedule.database(path) as con:
                con.execute(
                    "INSERT INTO raid_public_roster "
                    "(raid_id, position, name, assignment) VALUES (2,0,'Old','Hyjal')"
                )
            sheet._save_public_for_one(path, 1, [("Snore Mon", "Hyjal")])
            self.assertEqual(raid_schedule.read_public_roster(path, 2),
                             [("Old", "Hyjal")])
