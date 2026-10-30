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
            self.assertFalse(any(row[0].strip() == "WARRIOR" for row in body))
            self.assertEqual(len(body[0]), len(sheet.COLUMNS))
            self.assertEqual(by_character["Snore Mon"][8], "Signed")
            self.assertEqual(by_character["Snore Mon"][9:14],
                             ["Selected", "Yes", "No", "No", "No"])
            self.assertEqual(by_character["Snore Mon"][6], "Main tank")
            self.assertEqual(by_character["Snore Mon"][15], "Not submitted")
            self.assertTrue(by_character["Snore Mon"][7])
            self.assertTrue(by_character["Snore Mon"][16].isdigit())
            self.assertEqual(sheet._group(
                {"role": "DPS", "class_name": "Rogue", "spec": "Combat"}, False
            ), "Melee")
            self.assertEqual(sheet._group(
                {"role": "DPS", "class_name": "Mage", "spec": "Frost"}, False
            ), "Ranged")
            header = sheet._header(event, body, sheet.tab_title(event))
            self.assertEqual(header[6][:9], list(sheet.COLUMNS[:9]))
            self.assertEqual(header[1][3], "Forming")

    def test_more_than_two_tanks_repositions_headings_and_repairs_styles(self):
        """A third tank must not retain the former Healers heading's dark style."""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.setup_raid(path)
            for member_id in (45, 46):
                raid.save_character(member_id, f"Tank {member_id}",
                                    "Warrior", "Protection", "Tank", path)
                character = raid.member_characters(member_id, path)[0][0]
                raid.save_signup(1, member_id, character, path)

            first = sheet._header(event, sheet._rows(path, event, {}), sheet.tab_title(event))
            original = next(row for row in first if row[16] == "42")
            original[9:14] = ["Selected", "Yes", "No", "🔸", "No"]
            original[14] = "Keep me in Barrows"
            full = sheet._header(
                event, sheet._rows(path, event, {}, old_rows=first), sheet.tab_title(event))
            self.assertEqual(
                [row[1] for row in full[8:11]],
                ["Snore Mon", "Tank 45", "Tank 46"])
            self.assertEqual(full[7][0], "Tanks")
            self.assertEqual(full[12][0], "Healers")
            self.assertEqual(full[10][4], "Tank")
            self.assertEqual(full[11][0], "")
            original_after = next(row for row in full if row[16] == "42")
            self.assertEqual(original_after[9:14], ["Selected", "Yes", "No", "🔸", "No"])
            self.assertEqual(original_after[14], "Keep me in Barrows")

            sid = 6543210
            sheet._FORMATTED_SECTION_LAYOUTS.pop(sid, None)
            with patch.object(sheet.raid_sheets, "_json", return_value={}) as api:
                # Existing tabs must be repaired even when the cell data is unchanged.
                self.assertEqual(
                    sheet._write(object(), sheet.tab_title(event), sid, full, full), 0)
                api.assert_called_once()
                self.assertEqual(api.call_args.args[1], "POST")
                requests = api.call_args.kwargs["json"]["requests"]
                fills = [req["repeatCell"] for req in requests if "repeatCell" in req]
                self.assertEqual(fills[0]["range"]["startRowIndex"], 7)
                self.assertEqual(fills[0]["range"]["endRowIndex"], 500)
                self.assertEqual(fills[0]["cell"]["userEnteredFormat"]
                                 ["textFormat"]["bold"], False)
                headers = [
                    req["range"]["startRowIndex"] for req in fills[1:]
                    if req["cell"]["userEnteredFormat"]["textFormat"]["bold"]
                ]
                self.assertEqual(headers, [7, 12, 14, 16, 22])
                self.assertNotIn(10, headers)
                self.assertNotIn(11, headers)
                self.assertEqual(
                    fills[1]["cell"]["userEnteredFormat"]["backgroundColor"],
                    {"red": 0, "green": 0, "blue": 0},
                )
                api.reset_mock()
                self.assertEqual(
                    sheet._write(object(), sheet.tab_title(event), sid, full, full), 0)
                api.assert_not_called()
            sheet._FORMATTED_SECTION_LAYOUTS.pop(sid, None)

    def test_display_order_and_absence_timestamp(self):
        """Date fields are readable, and manual IDs retain their stable positions."""
        self.assertEqual(sheet.DISPLAY_ORDER[-2:], (18, 7))
        source = [""] * len(sheet.COLUMNS)
        source[7] = "2026-10-29T23:25:08+00:00"
        source[8] = "Absent"
        source[16] = "119863153411489793"
        source[17] = "1"
        source[18] = "2026-10-30T11:10:00+00:00"
        displayed = sheet._display(source)
        self.assertEqual(displayed[-1], "29 Oct 2026, 23:25")
        self.assertEqual(displayed[-2], "30 Oct 2026, 11:10")
        self.assertEqual(displayed[15:17], ["119863153411489793", "1"])
        restored = sheet._internal(displayed)
        self.assertEqual(restored[16], source[16])
        self.assertEqual(restored[17], source[17])
        self.assertEqual(restored[18], displayed[-2])
        self.assertEqual(sheet._display(restored), displayed)

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
            self.assertTrue(row[18])
            self.assertEqual(row[9:14], ["Not selected", "No", "No", "No", "No"])
            self.assertNotIn("Snore Mon", [r[1] for r in sheet._header(
                event, absent, sheet.tab_title(event)) if r[8] == "Signed" and r[9] == "Selected"])
            self.assertNotIn("42", [x[0] for x in sheet._approved(
                sheet._header(event, absent, sheet.tab_title(event)), event
            )])
            # Signing back on resets old instance assignments but restores signup defaults.
            raid.save_signup(1, 42, raid.member_characters(42, path)[0][0], path)
            restored = sheet._rows(path, event, {}, old_rows=sheet._header(
                event, absent, sheet.tab_title(event)
            ))
            row = next(row for row in restored if row[16] == "42")
            self.assertEqual(row[8], "Signed")
            self.assertEqual(row[9:14], ["Selected", "Yes", "No", "No", "No"])

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

    def test_live_barrows_roster_follows_sheet_visibility_before_confirmation(self):
        """Selected + Show Yes + an active Barrows group displays immediately."""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_raid(path)
            event = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Barrows (10m)")
            raid_schedule.record_panel(path, 1, 123456, 987654)
            event = raid_schedule.read_raid(path, 1)
            rows = sheet._header(event, sheet._rows(path, event, {}),
                                 sheet.tab_title(event))
            tank = next(row for row in rows if row[16] == "42")
            tank[9:14] = ["Selected", "Yes", "No", "🔸", "No"]
            rogue = next(row for row in rows if row[16] == "43")
            rogue[9:14] = ["Selected", "No", "No", "🔹", "No"]
            mage = next(row for row in rows if row[16] == "44")
            mage[9:14] = ["Selected", "Yes", "No", "No", "No"]
            session = unittest.mock.MagicMock()
            with patch.object(sheet.raid_sheets, "configured", return_value=True), \
                 patch.object(sheet.raid_sheets, "_session", return_value=session), \
                 patch.object(sheet.raid_sheets, "_raid_snapshot",
                              return_value=([], {1: event}, [], [], [])), \
                 patch.object(sheet, "_list_tabs",
                              return_value={sheet.tab_title(event): {"sheetId": 1}}), \
                 patch.object(sheet, "_one", return_value=(0, rows)), \
                 patch.object(sheet, "_sync_permanent"), \
                 patch.object(sheet, "_hide_legacy"):
                _, changed = sheet.sync_once(path)
                self.assertEqual(changed, {1})
                self.assertEqual(
                    raid_schedule.read_public_roster(path, 1, include_details=True),
                    [("Snore Mon", "Barrows 🔸", "Warrior", "Tank", "Protection")])
                self.assertEqual(raid_schedule.read_raid(path, 1)["roster_published"], 0)
                self.assertEqual(raid_schedule.read_raid(path, 1)["signup_open"], 1)
                rogue[10] = "Yes"
                _, changed = sheet.sync_once(path)
                self.assertEqual(changed, {1})
                self.assertEqual(
                    [name for name, *_ in raid_schedule.read_public_roster(path, 1)],
                    ["Snore Mon", "Fast Rogue"])
                tank[10] = "No"
                _, changed = sheet.sync_once(path)
                self.assertEqual(changed, {1})
                self.assertEqual(raid_schedule.read_public_roster(path, 1),
                                 [("Fast Rogue", "Barrows 🔹")])

    def test_confirm_lock_absences_and_archive_keep_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.setup_raid(path)
            self.assertIn("**Roster confirmation:**", raid_schedule.raid_roster_status(event))
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
        ), "<:Warrior:1565482101585748109> Snore Mon")
        self.assertEqual(raid_schedule.roster_name_with_emojis(
            "Mage One", "Mage", "DPS", "Frost"
        ), "<:Mage:1565481787231182969> Mage One")
        text = raid_schedule.roster_name_with_emojis(
            "Rogue One", "Rogue", "DPS", "Combat"
        )
        self.assertEqual(
            text, "<:Rogue:1565481939522297936> Rogue One"
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
