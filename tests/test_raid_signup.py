"""Offline tests of isolated raid sign-up preview and attendance schema."""
import json
import discord
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch

from cogs import raid_schedule, raid_poster

import cogs.raid_signup as raid


class RaidPrototypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_attendance_fields_and_status_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with raid.database(path) as con:
                tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"characters", "raids", "signups", "raid_absences", "gear_exports", "attendance"} <= tables)
                fields = {row[1] for row in con.execute("PRAGMA table_info(attendance)")}
                self.assertTrue({"raid_id", "discord_id", "character_id", "status", "late_minutes", "afk_minutes", "leadership_notes"} <= fields)
        self.assertEqual(set(raid.ATTENDANCE_OPTIONS), {"Pending", "Attended", "Late", "AFK", "No-show", "Excused"})
        self.assertIn("Attendance", raid.SHEET_TABS)

    async def test_raid_panel_three_hour_window_handles_midnight_and_dst(self):
        for start, date_line in (
            ("2026-12-09T21:00:00+00:00", "Wednesday, 9 December 2026"),
            ("2026-07-01T21:00:00+01:00", "Wednesday, 1 July 2026"),
            ("2027-03-28T00:30:00+00:00", "Sunday, 28 March 2027"),
        ):
            with self.subTest(start=start):
                output = raid_schedule.raid_panel_when(start)
                self.assertRegex(output, r"^<t:\d+:D> · <t:\d+:t> - <t:\d+:t>$")
                self.assertRegex(output, r"<t:\d+:t> - <t:\d+:t>")
                self.assertEqual(len(output.split("\n")), 1)
                self.assertNotIn(":F>", output)
        # A customised end is a separate Discord timestamp (not a static clock).
        custom = raid_schedule.raid_panel_when(
            "2026-12-09T21:00:00+00:00", "2026-12-10T01:30:00+00:00"
        )
        expected_end = raid_schedule.format_discord_date(
            "2026-12-10T01:30:00+00:00", style="t", with_relative=False
        )
        self.assertTrue(custom.endswith(" - " + expected_end))

    async def test_schedule_migration_and_v2_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.ensure_raid_fields(path)  # startup migration is safe to repeat
            event = raid_schedule.save_raid(
                path, 1, "2026-11-05 20:00", "Molten Core",
                roster_announce="2026-11-04 19:00",
            )
            self.assertEqual(event["raid_id"], 1)
            self.assertIsNone(event["instance_2"])
            panel = raid_schedule.RaidEventPanel(
                event, on_register=AsyncMock(), on_characters=AsyncMock()
            )
            text = json.dumps(panel.to_components())
            self.assertIn("<Hyped> Raid #1", text)
            self.assertIn("**Instances:** Molten Core", text)
            self.assertNotIn("Raid instance #1:", text)
            self.assertIn("asI88C.png", text)
            self.assertNotIn("y6bCxy.png", text)
            self.assertNotIn("XIszIH.png", text)
            self.assertIn("Roster confirmation:", text)
            self.assertNotIn("**Check:**", text)
            self.assertIn(" · <t:", raid_schedule.raid_panel_text(event))
            self.assertRegex(
                raid_schedule.raid_panel_text(event),
                r"<t:\d+:D> · <t:\d+:t> - <t:\d+:t>\n\*\*Instances:",
            )
            self.assertNotIn("**Date:**", text)
            self.assertNotIn("**Time:**", text)
            self.assertRegex(raid_schedule.raid_panel_text(event),
                             r"\*\*Roster confirmation:\*\* <t:\d+:R>")
            self.assertNotIn("Collecting gamers", text)
            self.assertNotIn("### Roster", text)
            self.assertNotIn("### Roster", raid_schedule.raid_panel_text(event))
            self.assertIn("**Roster confirmation:**", text)
            visible = raid_schedule.raid_panel_text(
                event, [("Snore Mon", "Ony, Hyjal"), ("Healer Main", "Barrows A")]
            )
            self.assertIn("Snore Mon", visible)
            self.assertIn("Healer Main", visible)
            self.assertNotIn("• ", visible)
            self.assertGreater(visible.index("**Roster confirmation:**"), visible.index("Snore Mon"))
            self.assertNotIn("Applicant", visible)
            self.assertNotIn("Sign-ups hidden", text)
            self.assertNotIn("WGNGdr.png", text)
            self.assertNotIn("SoftRes sign-up", text)
            self.assertFalse(any(isinstance(c, raid_schedule.discord.ui.MediaGallery)
                                 for c in panel.children))
            # A new raid should have no decorative bottom image.
            # The headline, roster status and action buttons stay in place.
            components = panel.to_components()
            self.assertEqual(len(components), 2)
            self.assertEqual(components[0]["type"], 17)  # Container
            self.assertEqual(components[1]["type"], 1)   # Action row of buttons
            self.assertEqual(components[0]["components"][0]["type"], 9)  # Section
            self.assertFalse(any(item["type"] == 12
                                 for item in components[0]["components"]))
            self.assertLess(text.index("Roster confirmation:"), text.index('"label": "Attend"'))
            self.assertIn("<t:", text)
            self.assertNotIn("Snore Mon", text)
            self.assertTrue(panel.is_persistent())
            self.assertIn('"disabled": true', text)
            event = raid_schedule.save_raid(
                path, 1, "2026-11-06 20:00", "Molten Core",
                "Onyxia", "Blackwing Lair", "2026-11-05 18:00",
            )
            updated = json.dumps(raid_schedule.RaidEventPanel(
                event, on_register=AsyncMock(), on_characters=AsyncMock()
            ).to_components())
            self.assertIn("**Instances:** Molten Core, Onyxia, Blackwing Lair", updated)
            self.assertNotIn("Raid instance #", updated)
            self.assertEqual(raid_schedule.latest_raid(path)["raid_id"], 1)
            raid_schedule.record_panel(path, 1, raid.TEST_CHANNEL_ID, 12345)
            self.assertEqual(raid_schedule.active_panels(path)[0]["panel_message_id"], 12345)

    async def test_hyjal_has_one_roster_and_barrows_keeps_two_diamond_teams(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            event = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)",
                "Barrows (10m)", "Ony (40m)",
                roster_announce="2026-12-08 21:00",
            )
            empty = raid_schedule.raid_panel_text(event)
            self.assertNotIn("### Roster", empty)
            self.assertNotIn("**Hyjal**", empty)
            self.assertNotIn("**Barrows**", empty)
            self.assertNotIn("**Ony**", empty)
            self.assertIn("**Roster confirmation:**", empty)
            members = [
                ("Snore Mon", "Hyjal, Barrows 🔸, Ony"),
                ("Healer One", "Hyjal, Barrows 🔹"),
                ("Healer Two", "Barrows 🔹"),
                ("Default Gamer", "Hyjal"),
                ("Pug Main", "Ony"),
                ("Gamer TBC", ""),
            ]
            grouped = raid_schedule.roster_groups(event, members)
            self.assertEqual(grouped, [
                ("Hyjal · 20-player",
                 ["Snore Mon", "Healer One", "Default Gamer"]),
                ("Barrows · 10-player 🔸", ["Snore Mon"]),
                ("Barrows · 10-player 🔹", ["Healer One", "Healer Two"]),
                ("Ony · 40-player", ["Snore Mon", "Pug Main"]),
                ("Selected · group pending", ["Gamer TBC"]),
            ])
            # A confirmed event switches from role signups to instance teams.
            confirmed_event = {**event, "roster_published": 1}
            text = raid_schedule.raid_panel_text(confirmed_event, members)
            self.assertIn("**Hyjal**", text)
            self.assertEqual(text.count("**Hyjal**"), 1)
            self.assertIn("**Barrows**", text)
            self.assertIn("**Ony**", text)
            self.assertNotIn("20-player", text)
            self.assertNotIn("10-player", text)
            self.assertNotIn("40-player", text)
            self.assertNotIn("**Hyjal · 20-player 🔸**", text)
            self.assertNotIn("**Hyjal · 20-player 🔹**", text)
            self.assertEqual(text.count("Snore Mon"), 3)
            self.assertEqual(text.count("Healer One"), 2)
            self.assertNotIn("• ", text)
            self.assertNotIn("Group A", text)
            self.assertNotIn("Group B", text)
            self.assertNotIn("Collecting gamers", text)

            panel = raid_schedule.RaidEventPanel(
                confirmed_event, on_signup=AsyncMock(), on_update=AsyncMock(),
                on_gear=AsyncMock(), on_absent=AsyncMock(),
                public_roster=members,
            )
            rendered = panel.to_components()
            main = rendered[0]["components"]
            self.assertEqual(main[0]["type"], 9)   # Intro + thumbnail
            self.assertEqual(rendered[0]["accent_color"], raid_schedule.RAID_PANEL_ACCENT)
            self.assertEqual(raid_schedule.RAID_PANEL_ACCENT, 0xB84A3A)
            self.assertNotEqual(raid_schedule.ACCENT, raid_schedule.RAID_PANEL_ACCENT)
            self.assertNotIn(12, [item["type"] for item in main])  # No default banner
            self.assertLess(len(main), 11) # Discord container component budget
            self.assertNotIn("discord.com/channels/", json.dumps(rendered))
            self.assertFalse(any(
                isinstance(item, discord.ui.Button)
                and item.style == discord.ButtonStyle.link
                for item in panel.walk_children()
            ))
            self.assertEqual(
                [button.label for button in panel.walk_children()
                 if isinstance(button, discord.ui.Button)],
                ["Attend", None, "Characters", "SimC", "Absent"],
            )
            self.assertIn("Roster confirmed:", json.dumps(rendered))
            self.assertNotIn("y6bCxy.png", json.dumps(rendered))

            # Old A/B sheet values are still accepted for the two Barrows teams.
            self.assertEqual(raid_schedule._split_from_tokens({"group a"}), "🔸")
            self.assertEqual(raid_schedule._split_from_tokens({"group b"}), "🔹")

            hyjal_only = raid_schedule.save_raid(
                path, 2, "2026-12-10 20:00", "Hyjal (20m)"
            )
            hyjal_text = raid_schedule.raid_panel_text(hyjal_only)
            self.assertNotIn("### Roster", hyjal_text)
            self.assertNotIn("**Hyjal**", hyjal_text)
            self.assertNotIn("\n\n\n", hyjal_text)

            barrows_only = raid_schedule.save_raid(
                path, 3, "2026-12-11 20:00", "Barrows (10m)"
            )
            barrows_text = raid_schedule.raid_panel_text(
                {**barrows_only, "roster_published": 1},
                [("Split Two", "Barrows 🔹")]
            )
            self.assertIn("**Barrows** 🔸\n-# **Tanks**", barrows_text)
            self.assertIn("-# **DPS**\nSplit Two", barrows_text)
            self.assertNotIn("**Hyjal**", barrows_text)
            self.assertNotIn("**Ony**", barrows_text)

    async def test_hunter_specs_use_forever_melee_and_ranged_groups(self):
        for spec, expected in (
            ("Survival", "Melee"),
            ("Beast Mastery", "Ranged"),
            ("Marksmanship", "Ranged"),
        ):
            self.assertEqual(
                raid_schedule.signup_role_group("Hunter", spec, "DPS"),
                expected,
            )
        grouped = raid_schedule.signup_role_groups([
            ("Surv Hunter", "", "Hunter", "DPS", "Survival"),
            ("BM Hunter", "", "Hunter", "DPS", "Beast Mastery"),
            ("MM Hunter", "", "Hunter", "DPS", "Marksmanship"),
        ])
        self.assertEqual([label for label, _ in grouped], ["Melee", "Ranged"])
        self.assertIn("Surv Hunter", grouped[0][1][0])
        self.assertEqual(len(grouped[1][1]), 2)

    async def test_collecting_roster_shows_role_groups_then_confirmed_teams(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(raid, "RAID_DB", str(Path(tmp) / "raid.db")):
            raid.create_schema(raid.RAID_DB)
            raid_schedule.ensure_raid_fields(raid.RAID_DB)
            event = raid_schedule.save_raid(
                raid.RAID_DB, 5, "2026-12-09 21:00", "Hyjal (20m)",
                "Barrows (10m)", "Ony (40m)")
            players = [
                (101, "Tank Test", "Warrior", "Protection", "Tank"),
                (102, "Melee Test", "Rogue", "Combat", "DPS"),
                (103, "Ranged Test", "Mage", "Frost", "DPS"),
                (104, "Healer Test", "Druid", "Restoration", "Healer"),
            ]
            for member_id, name, klass, spec, role in players:
                raid.save_character(member_id, name, klass, spec, role, raid.RAID_DB)
                character_id = raid.member_characters(member_id, raid.RAID_DB)[0][0]
                raid.save_signup(5, member_id, character_id, raid.RAID_DB)

            roster = raid_schedule.read_signup_roster(raid.RAID_DB, 5)
            self.assertEqual([entry[0] for entry in roster],
                             [entry[1] for entry in players])
            groups = raid_schedule.signup_role_groups(roster)
            self.assertEqual([name for name, _ in groups],
                             ["Tanks", "Melee", "Ranged", "Healers"])
            for role, members in groups:
                self.assertEqual(len(members), 1)
                self.assertIn("<:", members[0])  # Existing class icon markup.
            collecting = raid_schedule.raid_panel_text(event, roster)
            self.assertIn("**Sign-ups (4)**", collecting)
            self.assertIn("-# **Tanks (1)**", collecting)
            self.assertIn("-# <:Warrior:", collecting)
            self.assertIn("**Tanks (1)**", collecting)
            self.assertIn("**Melee (1)**", collecting)
            self.assertIn("**Ranged (1)**", collecting)
            self.assertIn("**Healers (1)**", collecting)
            self.assertNotIn("**Hyjal**", collecting)
            self.assertNotIn("**Barrows**", collecting)
            self.assertNotIn("**Ony**", collecting)
            cog = raid.RaidPrototype(SimpleNamespace())
            pre_panel = json.dumps(cog.make_panel(event).to_components())
            self.assertIn("Sign-ups (4)", pre_panel)
            self.assertNotIn("**Barrows**", pre_panel)
            # Private assignment edits must never turn on team display early.
            from cogs import raid_sheets_v3
            raid_sheets_v3._save_public_for_one(
                raid.RAID_DB, 5, [("Tank Test", "Barrows 🔸", "Warrior", "Tank", "Protection")])
            pre_panel = json.dumps(cog.make_panel(event).to_components())
            self.assertIn("Sign-ups (4)", pre_panel)
            self.assertNotIn("**Barrows**", pre_panel)

            confirmed_entries = [
                (str(member_id), name, assignment, klass, role, spec)
                for (member_id, name, klass, spec, role), assignment in zip(
                    players, ("Hyjal, Barrows 🔸", "Barrows 🔹",
                              "Ony", "Hyjal"))
            ]
            confirmed_event = raid_schedule.raid_status(
                raid.RAID_DB, 5, "confirm", confirmed_entries=confirmed_entries)
            raid_sheets_v3._save_public_for_one(
                raid.RAID_DB, 5, [(x[1], x[2], x[3], x[4], x[5])
                                  for x in confirmed_entries])
            confirmed_panel = json.dumps(cog.make_panel(confirmed_event).to_components())
            self.assertIn("### Roster", confirmed_panel)
            self.assertIn("**Hyjal**", confirmed_panel)
            self.assertIn("**Barrows**", confirmed_panel)
            self.assertIn("**Ony**", confirmed_panel)
            self.assertNotIn("Sign-ups (4)", confirmed_panel)
            self.assertEqual(confirmed_event["signup_open"], 0)

            # Signing off while still collecting removes that name immediately.
            raid_schedule.raid_status(raid.RAID_DB, 5, "reopen")
            raid.save_absence(5, 102, "Not available", raid.RAID_DB)
            self.assertEqual([r[0] for r in raid_schedule.read_signup_roster(
                raid.RAID_DB, 5)],
                ["Tank Test", "Ranged Test", "Healer Test"])

    async def test_optional_raid_title_round_trip_edit_preserve_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            event = raid_schedule.save_raid(
                path, 7, "2026-12-09 21:00", "Hyjal (20m)",
                custom_title="First Clear",
            )
            self.assertEqual(event["title"], "<Hyped> Raid #7 - First Clear")
            self.assertEqual(event["custom_title"], "First Clear")
            self.assertIn(
                "## <Hyped> Raid #7 - First Clear",
                raid_schedule.raid_panel_intro(event),
            )
            draft = raid_poster.draft_from_event(event)
            self.assertEqual(draft["custom_title"], "First Clear")
            self.assertEqual(
                raid_poster.draft_from_event(None)["custom_title"], ""
            )
            mock_cog = SimpleNamespace(
                raid_db_path=lambda: path, allowed=lambda interaction: True,
            )
            poster = raid_poster.RaidPosterView(mock_cog, 42, draft)
            self.assertIn("Optional Raid Title", json.dumps(poster.to_components()))
            self.assertIn("First Clear", poster.description())
            controls = [
                item.label for item in poster.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(controls, [
                "Optional Raid Title", "Bottom Banner", "Optional SoftRes",
                "Remove SoftRes", "Check Message Link", "Remove Check Link",
                "Post / Update Raid", "Preview / Confirm Roster",
                "Manage Raid", "Delete Raid",
            ])
            # Old /raid setup commands must preserve an existing subtitle.
            updated = raid_schedule.save_raid(
                path, 7, "2026-12-09 20:30", "Hyjal (20m)"
            )
            self.assertEqual(updated["custom_title"], "First Clear")
            self.assertEqual(updated["title"], "<Hyped> Raid #7 - First Clear")
            # Explicitly blank poster title clears the suffix.
            cleared = raid_schedule.save_raid(
                path, 7, "2026-12-09 20:30", "Hyjal (20m)",
                custom_title="",
            )
            self.assertEqual(cleared["custom_title"], "")
            self.assertEqual(cleared["title"], "<Hyped> Raid #7")
            with self.assertRaises(ValueError):
                raid_schedule.save_raid(
                    path, 7, "2026-12-09 20:30", "Hyjal (20m)",
                    custom_title="x" * 71,
                )
            self.assertEqual(
                raid_schedule.read_raid(path, 7)["title"], "<Hyped> Raid #7"
            )

    async def test_custom_bottom_banner_uses_gallery_and_preserves_posted_raid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            event = raid_schedule.save_raid(
                path, 8, "2026-12-09 21:00", "Hyjal (20m)",
            )
            self.assertIsNone(event["banner_url"])
            self.assertIn("**Bottom banner:** None (optional)",
                          raid_poster.RaidPosterView(
                              SimpleNamespace(raid_db_path=lambda: path,
                                              allowed=lambda interaction: True),
                              42, raid_poster.draft_from_event(event)
                          ).description())
            self.assertNotIn("y6bCxy.png", json.dumps(
                raid_schedule.RaidEventPanel(event).to_components()
            ))
            self.assertFalse(any(item["type"] == 12 for item in
                raid_schedule.RaidEventPanel(event).to_components()[0]["components"]))
            selected = "https://i.vgy.me/example.png"
            event = raid_schedule.save_raid(
                path, 8, "2026-12-09 21:00", "Hyjal (20m)",
                banner_url=selected,
            )
            self.assertEqual(event["banner_url"], selected)
            custom_components = raid_schedule.RaidEventPanel(event).to_components()
            self.assertIn(selected, json.dumps(custom_components))
            self.assertEqual(
                custom_components[0]["components"][-1]["type"], 12
            )  # Explicitly chosen image stays in the raid panel.
            self.assertNotIn("y6bCxy.png", json.dumps(
                raid_schedule.RaidEventPanel(event).to_components()
            ))
            draft = raid_poster.draft_from_event(event)
            self.assertEqual(draft["banner_url"], selected)
            poster = raid_poster.RaidPosterView(
                SimpleNamespace(raid_db_path=lambda: path,
                                allowed=lambda interaction: True), 42, draft,
            )
            self.assertIn("**Bottom banner:** Custom image", poster.description())
            modal = raid_poster.PosterTextModal(
                poster, 42, 55, "banner_url", "Bottom banner image",
                "HTTPS image URL", "https://i.vgy.me/test.png", required=False,
            )
            self.assertEqual(modal.text_field.max_length, 512)
            self.assertFalse(modal.text_field.required)
            event = raid_schedule.save_raid(
                path, 8, "2026-12-09 21:00", "Hyjal (20m)",
            )
            self.assertEqual(event["banner_url"], selected)
            event = raid_schedule.save_raid(
                path, 8, "2026-12-09 21:00", "Hyjal (20m)",
                banner_url="",
            )
            self.assertIsNone(event["banner_url"])
            self.assertNotIn("y6bCxy.png", json.dumps(
                raid_schedule.RaidEventPanel(event).to_components()
            ))
            self.assertFalse(any(item["type"] == 12 for item in
                raid_schedule.RaidEventPanel(event).to_components()[0]["components"]))
            for unsafe in ("http://example.com/art.png",
                           "https://localhost/art.png",
                           "https://example.com/art.txt"):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(ValueError):
                        raid_schedule.validate_banner_url(unsafe)

    async def test_optional_title_modal_is_non_required_and_plain_text_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            cog = SimpleNamespace(
                raid_db_path=lambda: path, allowed=lambda interaction: True
            )
            poster = raid_poster.RaidPosterView(cog, 42)
            modal = raid_poster.PosterTextModal(
                poster, 42, 55, "custom_title", "Optional raid title",
                "Text after Raid #", "First Clear", required=False,
            )
            self.assertFalse(modal.text_field.required)
            self.assertEqual(modal.text_field.max_length, 70)
            self.assertTrue(modal.title)
            event = raid_schedule.save_raid(
                path, 7, "2026-12-09 21:00", "Hyjal (20m)",
                custom_title="@everyone **new**",
            )
            intro = raid_schedule.raid_panel_intro(event)
            self.assertNotIn(" - @everyone **new**", intro)
            self.assertIn("@\u200beveryone", intro)
            self.assertNotIn("\n\n\n", intro)
            self.assertRegex(
                intro, r"<t:\d+:D> · <t:\d+:t> - <t:\d+:t>\n\*\*Instances:"
            )
            self.assertNotIn("**Date:**", intro)
            self.assertNotIn("**Time:**", intro)

    async def test_duplicate_characters_are_merged_without_losing_gear_or_raids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            # Emulate two old registrations saved under different realms and spacing.
            with raid.database(path) as con:
                con.execute("DROP INDEX idx_raid_characters_member_name")
                con.execute(
                    "INSERT INTO characters (discord_id,name,realm,class_name,spec,role) "
                    "VALUES (42,'Snore Mon','PvP','Mage','Frost','DPS')"
                )
                canonical = con.execute(
                    "SELECT character_id FROM characters WHERE discord_id=42"
                ).fetchone()[0]
                con.execute(
                    "INSERT INTO characters "
                    "(discord_id,name,realm,class_name,spec,role,is_main,last_simc_at) "
                    "VALUES (42,'  snore   mon  ','Other','Mage','Fire','DPS',0,12345)"
                )
                duplicate = con.execute(
                    "SELECT MAX(character_id) FROM characters"
                ).fetchone()[0]
                con.execute(
                    "INSERT INTO characters (discord_id,name,realm,class_name,spec,role) "
                    "VALUES (99,'Snore Mon','','Mage','Frost','DPS')"
                )
                con.execute(
                    "INSERT INTO signups (raid_id,discord_id,character_id,submitted_at) "
                    "VALUES (1,42,?,'2026-12-01T12:00:00+00:00')",
                    (duplicate,)
                )
                con.execute(
                    "INSERT INTO attendance (raid_id,discord_id,character_id) "
                    "VALUES (1,42,?)", (duplicate,)
                )
                con.execute(
                    "INSERT INTO gear_exports (character_id,submitted_at,format,raw_text) "
                    "VALUES (?,'2026-12-01T13:00:00+00:00','simc_raw','player=SnoreMon')",
                    (duplicate,)
                )
            raid.create_schema(path)
            raid.create_schema(path)  # migration is safe across repeated restarts
            with raid.database(path) as con:
                rows = con.execute(
                    "SELECT character_id,name,last_simc_at FROM characters "
                    "WHERE discord_id=42"
                ).fetchall()
                self.assertEqual(rows, [(canonical, "Snore Mon", 12345)])
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM characters WHERE discord_id=99"
                ).fetchone()[0], 1)
                for table in ("signups", "attendance", "gear_exports"):
                    self.assertEqual(con.execute(
                        f"SELECT character_id FROM {table} WHERE character_id=?",
                        (canonical,)
                    ).fetchone()[0], canonical)
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO characters "
                        "(discord_id,name,realm,class_name,spec,role) "
                        "VALUES (42,'SNORE MON','','Mage','Fire','DPS')"
                    )
            self.assertEqual(
                raid.save_character(42, "  snore  mon ", "Mage", "Arcane", "DPS", path),
                "updated",
            )
            self.assertEqual(raid.list_characters(42, path),
                             [("Snore Mon", "Mage", "Arcane", "DPS")])
            self.assertEqual(raid.my_signup(1, 42, path), ("Snore Mon", "Available"))

    async def test_repeat_signup_is_one_record_and_keeps_original_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            raid.save_character(42, "Firstname Lastname", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            raid.save_signup(1, 42, char_id, path)
            with raid.database(path) as con:
                con.execute(
                    "UPDATE signups SET submitted_at='2026-12-01T12:00:00+00:00'"
                )
            raid.save_signup(1, 42, char_id, path)
            with raid.database(path) as con:
                row = con.execute(
                    "SELECT COUNT(*), submitted_at FROM signups "
                    "WHERE raid_id=1 AND discord_id=42"
                ).fetchone()
            self.assertEqual(row, (1, "2026-12-01T12:00:00+00:00"))

    async def test_multiple_character_choices_still_only_one_signup_per_night(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            raid.save_character(42, "Snore Mon", "Mage", "Frost", "DPS", path)
            raid.save_character(42, "Second Character", "Warrior", "Fury", "DPS", path)
            characters = raid.member_characters(42, path)
            original = next(r[0] for r in characters if r[1] == "Snore Mon")
            replacement = next(r[0] for r in characters if r[1] == "Second Character")
            raid.save_signup(1, 42, original, path)
            raid.save_signup(1, 42, replacement, path)
            self.assertEqual(raid.my_signup(1, 42, path),
                             ("Second Character", "Available"))
            with raid.database(path) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM signups WHERE raid_id=1 AND discord_id=42"
                ).fetchone()[0], 1)

    async def test_optional_inline_softres_link_and_separate_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-11-05 20:00", "Molten Core")
            event = raid_schedule.set_softres_url(path, 1, "https://softres.it/raid/abc123")
            panel = raid_schedule.RaidEventPanel(
                event, on_register=AsyncMock(), on_characters=AsyncMock()
            )
            components = json.dumps(panel.to_components())
            self.assertIn("[SoftRes sign-up](https://softres.it/raid/abc123)", components)
            self.assertNotIn('"label": "SoftRes"', components)
            self.assertNotIn('"url": "https://softres.it/raid/abc123"', components)
            # Editing raid details without a link does not wipe the existing SoftRes.
            event = raid_schedule.save_raid(path, 1, "2026-11-06 20:00", "Onyxia")
            self.assertEqual(event["softres_url"], "https://softres.it/raid/abc123")
            event = raid_schedule.set_softres_url(path, 1, None)
            self.assertIsNone(event["softres_url"])
            self.assertNotIn("SoftRes sign-up", raid_schedule.raid_panel_text(event))
            self.assertEqual(raid_schedule.validate_softres_url(
                "https://legacy.softres.it/raid/gcz7uk"),
                "https://legacy.softres.it/raid/gcz7uk")
            with self.assertRaises(ValueError):
                raid_schedule.set_softres_url(path, 1, "http://softres.it/raid/abc123")
            with self.assertRaises(ValueError):
                raid_schedule.set_softres_url(path, 1, "https://example.com/raid/abc123")
            with self.assertRaises(ValueError):
                raid_schedule.set_softres_url(path, 999, "https://softres.it/raid/abc123")

    async def test_schedule_connections_are_closed_even_on_errors(self):
        """All raid schedule reads/writes must release SQLite handles on Windows."""
        class TrackedConnection(sqlite3.Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.was_closed = False

            def close(self):
                self.was_closed = True
                super().close()

        original_connect = sqlite3.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            connection = original_connect(
                *args, factory=TrackedConnection, **kwargs
            )
            opened.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with patch.object(raid_schedule.sqlite3, "connect",
                              side_effect=tracked_connect):
                raid_schedule.ensure_raid_fields(path)
                raid_schedule.save_raid(
                    path, 1, "2026-11-05 20:00", "Molten Core"
                )
                raid_schedule.read_raid(path, 1)
                raid_schedule.latest_raid(path)
                raid_schedule.active_panels(path)
                raid_schedule.record_panel(path, 1, raid.TEST_CHANNEL_ID, 7654)
                raid_schedule.set_softres_url(
                    path, 1, "https://softres.it/raid/abc123"
                )
                with self.assertRaises(ValueError):
                    raid_schedule.set_softres_url(
                        path, 999, "https://softres.it/raid/abc123"
                    )
            self.assertTrue(opened)
            self.assertTrue(all(con.was_closed for con in opened),
                            "A raid scheduling SQLite connection was left open")

    async def test_poster_menu_and_schedule_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            dummy_cog = SimpleNamespace(raid_db_path=lambda: path,
                                        allowed=lambda interaction: True)
            poster = raid_poster.RaidPosterView(dummy_cog, 42)
            components = json.dumps(poster.to_components())
            self.assertIn("Raid Poster", components)
            self.assertIn("XIszIH.png", components)
            self.assertEqual(poster.to_components()[0]["accent_color"], raid_schedule.ACCENT)
            self.assertNotIn("asI88C.png", components)
            self.assertNotIn("y6bCxy.png", components)
            self.assertIn("Choose raid date", components)
            self.assertIn("Choose start time", components)
            self.assertIn("Other start time (HH:MM)", components)
            self.assertIn("Choose end time", components)
            self.assertIn("Other time (HH:MM)", components)
            self.assertIn("Check Message Link", components)
            self.assertIn("Remove Check Link", components)
            self.assertNotIn("Choose Check channel", components)
            self.assertIn("**Check message:** Not set (optional)", poster.description())
            self.assertIn("Raid instance #1", components)
            self.assertIn("Raid instance #2", components)
            self.assertIn("Raid instance #3", components)
            self.assertIn("Optional Raid Title", components)
            self.assertIn("Bottom Banner", components)
            self.assertIn("Optional SoftRes", components)
            self.assertIn("Post / Update Raid", components)
            self.assertIn("Choose exact announcement date & time", components)
            self.assertIn("When to announce the roster?", components)
            self.assertIn("New Raid #1", components)
            self.assertNotIn("Signed up:", components)
            draft = raid_poster.draft_from_event(None)
            draft.update(number=1, day="2026-11-05", instance_1="Molten Core")
            at, announce = raid_poster.poster_schedule(draft)
            self.assertEqual(at, "2026-11-05 20:00")
            self.assertEqual(announce, "2026-11-04 20:00")
            event = raid_schedule.save_raid(path, 1, at, "Molten Core",
                                            roster_announce=announce)
            self.assertEqual(event["signup_open"], 1)
            draft2 = raid_poster.draft_from_event(event)
            self.assertEqual(draft2["number"], 1)
            self.assertEqual(draft2["day"], "2026-11-05")
            self.assertEqual(draft2["instance_1"], "Molten Core")
            self.assertIsNone(draft2["end_time"])
            self.assertIsNone(draft2["check_message_url"])
            self.assertIn("Edit Raid #1", json.dumps(
                raid_poster.RaidPosterView(dummy_cog, 42).to_components()
            ))

    async def test_custom_end_time_and_check_message_roundtrip_and_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.ensure_raid_fields(path)
            draft = raid_poster.draft_from_event(None)
            draft.update(
                day="2026-12-09", time="21:00", end_time="01:30",
                instance_1="Hyjal (20m)",
                check_message_url="https://discord.com/channels/1564000000000000001/1564000000000000002/1564000000000000003",
            )
            end_at = raid_poster.poster_end_at(draft)
            self.assertTrue(end_at.startswith("2026-12-10T01:30:00"))
            event = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)",
                end_at=end_at, check_message_url=draft["check_message_url"]
            )
            self.assertEqual(event["end_at"], end_at)
            self.assertEqual(event["check_message_url"], draft["check_message_url"])
            self.assertIn("**Check:** [View message](", raid_schedule.raid_panel_intro(event))
            self.assertIn(" - " + raid_schedule.format_discord_date(
                end_at, style="t", with_relative=False
            ), raid_schedule.raid_panel_intro(event))
            recovered = raid_poster.draft_from_event(event)
            self.assertEqual(recovered["end_time"], "01:30")
            self.assertEqual(recovered["check_message_url"], draft["check_message_url"])
            # Old setup callers must not erase either new setting.
            legacy = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)"
            )
            self.assertEqual(legacy["end_at"], end_at)
            self.assertEqual(legacy["check_message_url"], draft["check_message_url"])
            moved = raid_schedule.save_raid(
                path, 1, "2026-12-11 20:00", "Hyjal (20m)"
            )
            self.assertTrue(moved["end_at"].startswith("2026-12-12T00:30"))
            self.assertEqual(moved["check_message_url"], draft["check_message_url"])
            # Explicit default selection clears custom end, but keeps Check permalink.
            reset = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)", end_at=None
            )
            self.assertIsNone(reset["end_at"])
            self.assertEqual(reset["check_message_url"], draft["check_message_url"])
            cleared = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)", check_message_url=None
            )
            self.assertIsNone(cleared["check_message_url"])
            self.assertNotIn("**Check:**", raid_schedule.raid_panel_intro(cleared))
            with raid.database(path) as con:
                cols = {r[1] for r in con.execute("PRAGMA table_info(raids)")}
                self.assertTrue({"end_at", "check_message_url"} <= cols)

    async def test_poster_rejects_invalid_end_times_and_accepts_after_midnight(self):
        draft = raid_poster.draft_from_event(None)
        draft.update(day="2026-12-09", time="21:00", instance_1="Hyjal (20m)")
        self.assertIsNone(raid_poster.poster_end_at(draft))
        for end_time in ("21:00", "20:00", "garbage", "25:00"):
            with self.subTest(end_time=end_time):
                draft["end_time"] = end_time
                with self.assertRaises(ValueError):
                    raid_poster.poster_end_at(draft)
        draft["end_time"] = "00:00"
        self.assertTrue(raid_poster.poster_end_at(draft).startswith("2026-12-10T00:00"))
        draft["end_time"] = "23:00"
        self.assertTrue(raid_poster.poster_end_at(draft).startswith("2026-12-09T23:00"))

    async def test_signup_hidden_and_gear_check_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            event = raid_schedule.save_raid(
                path, 1, "2026-11-05 20:00", "Molten Core"
            )
            panel = raid_schedule.RaidEventPanel(
                event, on_register=AsyncMock(), on_characters=AsyncMock(),
                on_signup=AsyncMock(), on_gear=AsyncMock(),
                on_update=AsyncMock(), on_absent=AsyncMock(),
            )
            components = json.dumps(panel.to_components())
            self.assertNotIn("Collecting gamers", components)
            self.assertIn('"label": "Attend"', components)
            self.assertIn('"style": 3', components)
            self.assertIn('"label": "Characters"', components)
            self.assertIn('"label": "SimC"', components)
            self.assertIn('"label": "Absent"', components)
            self.assertNotIn('"label": "Register character"', components)
            self.assertNotIn('"label": "My characters"', components)
            self.assertNotIn("Signed up:", components)
            # Existing panels get active signup buttons when an on_signup is bound.
            buttons = [item for item in panel.walk_children()
                       if isinstance(item, discord.ui.Button)]
            self.assertFalse(next(b for b in buttons if b.label == "Attend").disabled)
            self.assertEqual([b.label for b in buttons],
                             ["Attend", None, "Characters", "SimC", "Absent"])
            self.assertFalse(all(b.disabled for b in buttons))
            self.assertEqual(buttons[-1].style, discord.ButtonStyle.danger)
            raid.save_character(42, "Snore Mon", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            # Registration and signup must work before any SimC gear upload.
            raid.save_signup(1, 42, char_id, path)
            self.assertEqual(raid.my_signup(1, 42, path), ("Snore Mon", "Available"))
            with self.assertRaises(ValueError):
                raid.save_gear(999, char_id, "player=SnoreMon\nlevel=60\n" * 5, path)
            raid.save_gear(42, char_id, "player=SnoreMon\nlevel=60\n" * 5, path)
            raid.save_signup(1, 42, char_id, path)
            self.assertEqual(raid.my_signup(1, 42, path), ("Snore Mon", "Available"))
            with raid.database(path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM signups").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM gear_exports").fetchone()[0], 1)
            timestamp = raid.save_absence(1, 42, "Running late", path)
            self.assertTrue(timestamp.endswith("+00:00"))
            self.assertEqual(raid.my_signup(1, 42, path), ("Snore Mon", "Unavailable"))
            self.assertEqual(raid.my_absence(1, 42, path), ("Running late", timestamp))
            raid.save_signup(1, 42, char_id, path)
            self.assertEqual(raid.my_signup(1, 42, path), ("Snore Mon", "Available"))
            self.assertIsNone(raid.my_absence(1, 42, path))

    async def test_poster_publish_updates_existing_raid_and_removes_optional_softres(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(
                path, 1, "2026-11-05 20:00", "Molten Core",
                softres_url="https://softres.it/raid/abc123",
            )
            cog = SimpleNamespace(raid_db_path=lambda: path, allowed=lambda i: True,
                                  post_panel=AsyncMock())
            draft = raid_poster.draft_from_event(raid_schedule.read_raid(path, 1))
            draft.update(day="2026-11-06", instance_1="Onyxia's Lair",
                         instance_2=None, instance_3=None, softres_url=None,
                         end_time="00:30",
                          check_message_url="https://discord.com/channels/1564000000000000001/1564000000000000002/1564000000000000003")
            poster = raid_poster.RaidPosterView(cog, 42, draft)
            button = next(
                item for item in poster.walk_children()
                if isinstance(item, discord.ui.Button)
                and item.label == "Post / Update Raid"
            )
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), response=SimpleNamespace(
                    send_message=AsyncMock()
                )
            )
            await button.callback(interaction)
            saved = raid_schedule.read_raid(path, 1)
            self.assertEqual(saved["instance_1"], "Onyxia's Lair")
            self.assertEqual(saved["raid_at"][:10], "2026-11-06")
            self.assertEqual(saved["end_at"][:16], "2026-11-07T00:30")
            self.assertEqual(saved["check_message_url"], draft["check_message_url"])
            self.assertIsNone(saved["softres_url"])
            self.assertEqual(saved["signup_open"], 1)
            cog.post_panel.assert_awaited_once()
            self.assertNotIn("Sign-ups hidden", json.dumps(
                interaction.response.send_message.await_args.kwargs
            ) if interaction.response.send_message.await_args else "")

    async def test_poster_only_offers_forever_raid_instances(self):
        self.assertEqual(
            raid_poster.INSTANCES,
            ("Hyjal (20m)", "Barrows (10m)", "Ony (40m)"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            cog = SimpleNamespace(raid_db_path=lambda: path, allowed=lambda i: True)
            panel = raid_poster.RaidPosterView(cog, 42)
            payload = json.dumps(panel.to_components())
            for name in raid_poster.INSTANCES:
                self.assertIn(name, payload)
            self.assertNotIn("Molten Core", payload)
            self.assertNotIn("Blackwing Lair", payload)
            self.assertIn('"label": "Delete Raid"', payload)
            delete_button = next(
                item for item in panel.walk_children()
                if isinstance(item, discord.ui.Button) and item.label == "Delete Raid"
            )
            self.assertTrue(delete_button.disabled)
            raid_schedule.save_raid(path, 1, "2026-11-05 20:00", "Hyjal (20m)")
            panel = raid_poster.RaidPosterView(
                cog, 42, raid_poster.draft_from_event(
                    raid_schedule.read_raid(path, 1)
                )
            )
            delete_button = next(
                item for item in panel.walk_children()
                if isinstance(item, discord.ui.Button) and item.label == "Delete Raid"
            )
            self.assertFalse(delete_button.disabled)
            self.assertIn("Post destination", json.dumps(panel.to_components()))

    async def test_delete_raid_only_deletes_that_raids_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid.save_character(42, "Snore Mon", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            raid.save_gear(42, char_id, "player=Snore Mon\nlevel=60\n" * 5, path)
            for number in (1, 2):
                raid_schedule.save_raid(
                    path, number, "2026-11-05 20:00", "Hyjal (20m)"
                )
                raid.save_signup(number, 42, char_id, path)
                raid.save_absence(number, 99, "", path)
                with raid.database(path) as con:
                    con.execute(
                        "INSERT INTO attendance (raid_id, discord_id, character_id) "
                        "VALUES (?, 42, ?)", (number, char_id)
                    )
            raid_schedule.delete_raid(path, 1)
            self.assertIsNone(raid_schedule.read_raid(path, 1))
            self.assertIsNotNone(raid_schedule.read_raid(path, 2))
            self.assertIsNone(raid.my_signup(1, 42, path))
            self.assertEqual(raid.my_signup(2, 42, path), ("Snore Mon", "Available"))
            self.assertIsNone(raid.my_absence(1, 99, path))
            self.assertIsNotNone(raid.my_absence(2, 99, path))
            with raid.database(path) as con:
                self.assertEqual(
                    con.execute("SELECT COUNT(*) FROM attendance").fetchone()[0], 1
                )
                self.assertEqual(
                    con.execute("SELECT COUNT(*) FROM gear_exports").fetchone()[0], 1
                )
                self.assertEqual(
                    con.execute("SELECT COUNT(*) FROM characters").fetchone()[0], 1
                )
            with self.assertRaises(ValueError):
                raid_schedule.delete_raid(path, 1)

    async def test_raid_deletion_removes_discord_card_and_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(raid, "RAID_DB", str(Path(tmp) / "raid.db")), \
             patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            raid.create_schema(raid.RAID_DB)
            raid_schedule.ensure_raid_fields(raid.RAID_DB)
            raid_schedule.save_raid(
                raid.RAID_DB, 1, "2026-11-05 20:00", "Hyjal (20m)"
            )
            raid_schedule.record_panel(raid.RAID_DB, 1, raid.RAID_CHANNEL_ID, 7654)
            posted = SimpleNamespace(delete=AsyncMock(),
                                     author=SimpleNamespace(id=4444))
            destination = Mock(spec=discord.TextChannel)
            destination.guild = SimpleNamespace(id=77)
            destination.fetch_message = AsyncMock(return_value=posted)
            bot = SimpleNamespace(
                get_channel=lambda cid: destination, user=SimpleNamespace(id=4444))
            cog = raid.RaidPrototype(bot)
            poster_message = SimpleNamespace(edit=AsyncMock())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                message=poster_message,
                response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            confirm = raid_poster.RaidDeleteConfirmation(cog, 42, 1)
            self.assertIn("This cannot be undone", json.dumps(confirm.to_components()))
            self.assertIsNotNone(raid_schedule.read_raid(raid.RAID_DB, 1))
            button = next(
                item for item in confirm.walk_children()
                if isinstance(item, discord.ui.Button)
                and item.label == "Yes, delete Raid #1"
            )
            await button.callback(interaction)
            posted.delete.assert_awaited_once()
            self.assertIsNone(raid_schedule.read_raid(raid.RAID_DB, 1))
            self.assertIsInstance(
                poster_message.edit.await_args.kwargs["view"],
                raid_poster.RaidPosterView,
            )
            self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    async def test_poster_command_is_non_ephemeral_and_private_channel_only(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(raid, "RAID_DB", str(Path(tmp) / "raid.db")), \
             patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            raid.create_schema(raid.RAID_DB)
            raid_schedule.ensure_raid_fields(raid.RAID_DB)
            cog = raid.RaidPrototype(SimpleNamespace())
            channel = SimpleNamespace(send=AsyncMock(
                return_value=SimpleNamespace(jump_url="https://discord.com/channels/77/55/1")
            ))
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, channel=channel,
                response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            await cog.poster.callback(cog, interaction)
            self.assertIsInstance(
                channel.send.await_args.kwargs["view"], raid_poster.RaidPosterView
            )
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            interaction.channel_id = raid.RAID_CHANNEL_ID
            interaction.response.send_message.reset_mock()
            await cog.poster.callback(cog, interaction)
            self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
            channel.send.assert_awaited_once()

    async def test_uk_time_is_discord_localised_and_validated(self):
        summer = raid_schedule.parse_uk_datetime("2026-07-01 20:00")
        winter = raid_schedule.parse_uk_datetime("2026-11-01 20:00")
        self.assertEqual(summer.utcoffset().total_seconds(), 3600)
        self.assertEqual(winter.utcoffset().total_seconds(), 0)
        self.assertIn("<t:", raid_schedule.format_discord_date(summer.isoformat()))
        with self.assertRaises(ValueError):
            raid_schedule.parse_uk_datetime("tomorrow")
        with self.assertRaises(ValueError):
            raid_schedule.parse_uk_datetime("2026-03-29 01:30")
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            with self.assertRaises(ValueError):
                raid_schedule.save_raid(path, 1, "2026-11-05 20:00", "MC", roster_announce="2026-11-06 20:00")

    async def test_live_panel_posts_normal_message_then_edits_same_message(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(raid, "RAID_DB", str(Path(tmp) / "raid.db")), \
             patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            raid.create_schema(raid.RAID_DB)
            raid_schedule.ensure_raid_fields(raid.RAID_DB)
            response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
            message = SimpleNamespace(id=7654,
                jump_url="https://discord.com/channels/77/55/7654", edit=AsyncMock())
            channel = Mock(spec=discord.TextChannel)
            channel.id = raid.TEST_CHANNEL_ID
            channel.guild = SimpleNamespace(id=77, me=SimpleNamespace(), roles=[])
            channel.permissions_for.return_value = SimpleNamespace(
                view_channel=True, send_messages=True, mention_everyone=False)
            channel.send = AsyncMock(return_value=message)
            channel.fetch_message = AsyncMock(return_value=message)
            bot = SimpleNamespace(get_channel=lambda cid: channel)
            cog = raid.RaidPrototype(bot)
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
                channel=channel, followup=SimpleNamespace(send=AsyncMock()),
            )
            await cog.raid_setup.callback(cog, interaction, 2,
                                          "2026-11-05 20:00", "Molten Core")
            response.defer.assert_awaited_once_with(ephemeral=True)
            kwargs = channel.send.await_args.kwargs
            self.assertIsInstance(kwargs["view"], raid_schedule.RaidEventPanel)
            self.assertTrue(kwargs["view"].is_persistent())
            self.assertEqual(raid_schedule.read_raid(raid.RAID_DB, 2)["panel_message_id"], 7654)
            self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
            await cog.raid_setup.callback(cog, interaction, 2,
                                          "2026-11-05 20:00", "Onyxia")
            channel.send.assert_awaited_once()
            message.edit.assert_awaited_once()
            self.assertIn("Onyxia", json.dumps(message.edit.await_args.kwargs["view"].to_components()))
            response.send_message.reset_mock()
            interaction.channel_id = raid.RAID_CHANNEL_ID
            await cog.raid_setup.callback(cog, interaction, 3,
                                          "2026-11-06 20:00", "Onyxia")
            self.assertNotIn("view", response.send_message.await_args.kwargs)
            self.assertIsNone(raid_schedule.read_raid(raid.RAID_DB, 3))

    async def test_absence_without_character_and_optional_note_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-11-05 20:00", "Hyjal (20m)")
            moment = raid.save_absence(1, 99, path=path)
            self.assertEqual(raid.my_absence(1, 99, path), (None, moment))
            self.assertIsNone(raid.my_signup(1, 99, path))
            update = raid.save_absence(1, 99, "Away from keyboard", path)
            self.assertEqual(raid.my_absence(1, 99, path), ("Away from keyboard", update))
            with raid.database(path) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM raid_absences WHERE raid_id=1"
                ).fetchone()[0], 1)
            with self.assertRaises(ValueError):
                raid.save_absence(999, 99, path=path)
            with self.assertRaises(ValueError):
                raid.save_absence(1, 99, "x" * 501, path)

    async def test_absence_is_only_red_main_card_button_not_in_signup(self):
        with patch.object(raid, "member_characters", return_value=[]):
            view = raid.CharacterActionView(42, raid_id=9)
            data = json.dumps(view.to_components())
            self.assertNotIn('"label": "Absent"', data)
            self.assertNotIn("Withdraw my sign-up", data)
            self.assertNotIn("Applicant list remains hidden", data)
            self.assertIn("Choose the character", data)
            first_time = raid.ClassPickerView(42, signup_raid_id=9)
            self.assertNotIn('"label": "Absent"',
                             json.dumps(first_time.to_components()))
            modal = raid.AbsenceNoteModal(42, 9)
            self.assertEqual(modal.note.label, "Note (optional)")
            self.assertFalse(modal.note.required)
            self.assertEqual(modal.note.max_length, 500)

    async def test_character_profile_can_be_updated_without_signup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid.save_character(42, "Snore Mon", "Mage", "Frost", "DPS", path)
            raid.save_character(42, "snore mon", "Mage", "Fire", "DPS", path)
            self.assertEqual(raid.list_characters(42, path),
                             [("Snore Mon", "Mage", "Fire", "DPS")])
            self.assertEqual(raid.list_characters(99, path), [])
            with raid.database(path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM signups").fetchone()[0], 0)
                self.assertEqual(con.execute("SELECT realm FROM characters").fetchone()[0], "")

    async def test_old_realm_record_is_updated_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with raid.database(path) as con:
                con.execute(
                    "INSERT INTO characters (discord_id,name,realm,class_name,spec,role) "
                    "VALUES (42,'Snore Mon','PvP','Mage','Frost','DPS')"
                )
                old_id = con.execute("SELECT character_id FROM characters").fetchone()[0]
            raid.save_character(42, "Snore Mon", "Mage", "Fire", "DPS", path)
            with raid.database(path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM characters").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT character_id FROM characters").fetchone()[0], old_id)
            self.assertEqual(raid.list_characters(42, path),
                             [("Snore Mon", "Mage", "Fire", "DPS")])

    async def test_class_spec_role_validation(self):
        self.assertEqual(len(raid.CLASS_SPECS), 9)
        self.assertEqual(set(raid.CLASS_SPECS["Druid"]["Feral"]), {"Tank", "DPS"})
        self.assertEqual(raid.CLASS_SPECS["Mage"]["Frost"], ("DPS",))
        self.assertNotIn("Demon Hunter", raid.CLASS_SPECS)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with self.assertRaises(ValueError):
                raid.save_character(42, "Snore", "Mage", "Frost", "Tank", path)
            with self.assertRaises(ValueError):
                raid.save_character(42, "Snore", "Mage", "Retribution", "DPS", path)

    async def test_registration_uses_smart_selects_and_name_only_modal(self):
        class_data = json.dumps(raid.ClassPickerView(42).to_components())
        spec_data = json.dumps(raid.SpecPickerView(42, "Druid").to_components())
        roles = json.dumps(
            raid.RolePickerView(42, "Druid", "Feral", ("Tank", "DPS")).to_components()
        )
        self.assertIn("Choose your class", class_data)
        self.assertIn("Choose your specialisation", spec_data)
        self.assertIn('"label": "Feral"', spec_data)
        self.assertIn('"label": "Tank"', roles)
        self.assertIn('"label": "DPS"', roles)
        modal = raid.CharacterNameModal(42, "Mage", "Frost", "DPS")
        self.assertEqual(len(modal.children), 1)
        self.assertEqual(modal.children[0].label, "Character name")
        self.assertEqual(modal.children[0].placeholder, "Firstname Lastname")
        self.assertNotIn("no realm needed", json.dumps(raid.ClassPickerView(42).to_components()))

    async def test_signup_button_defers_and_sends_v2_view_without_content(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "read_raid", return_value={
                 "raid_id": 9, "signup_open": 1, "roster_published": 0,
             }), patch.object(raid, "member_characters", return_value=[
                 (12, "Firstname Lastname", "Mage", "Frost", "DPS", None),
             ]), patch.object(raid, "signup_note_details", return_value=(
                 "Firstname Lastname", "Available", "Old note",
             )):
            cog = raid.RaidPrototype(SimpleNamespace())
            response = SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock())
            followup = SimpleNamespace(send=AsyncMock())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
                followup=followup,
            )
            await cog.on_signup(interaction, 9)
            response.defer.assert_awaited_once_with(ephemeral=True)
            kwargs = followup.send.await_args.kwargs
            self.assertIsInstance(kwargs["view"], raid.ExistingAttendanceView)
            self.assertTrue(kwargs["ephemeral"])
            self.assertNotIn("content", kwargs)
            self.assertEqual(followup.send.await_args.args, ())
            self.assertIn("Already attending", json.dumps(kwargs["view"].to_components()))
            self.assertFalse(response.send_message.called)

    async def test_first_time_signup_opens_registration_with_raid_context(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "read_raid", return_value={
                 "raid_id": 9, "signup_open": 1, "roster_published": 0,
             }), patch.object(raid, "member_characters", return_value=[]):
            cog = raid.RaidPrototype(SimpleNamespace())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            await cog.on_signup(interaction, 9)
            kwargs = interaction.followup.send.await_args.kwargs
            self.assertIsInstance(kwargs["view"], raid.ClassPickerView)
            self.assertEqual(kwargs["view"].signup_raid_id, 9)
            self.assertNotIn("content", kwargs)
            self.assertIn("Raid #9", json.dumps(kwargs["view"].to_components()))
            spec = raid.SpecPickerView(42, "Mage", signup_raid_id=9)
            self.assertEqual(spec.signup_raid_id, 9)
            role = raid.RolePickerView(42, "Druid", "Feral",
                                       ("Tank", "DPS"), signup_raid_id=9)
            self.assertEqual(role.signup_raid_id, 9)

    async def test_guild_member_can_use_raid_controls_in_public_channel(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "read_raid", return_value={
                 "raid_id": 9, "signup_open": 1, "roster_published": 0,
             }), patch.object(raid, "member_characters", return_value=[]):
            cog = raid.RaidPrototype(SimpleNamespace())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=99), guild_id=77,
                channel_id=raid.RAID_CHANNEL_ID,
                response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            self.assertFalse(cog.allowed(interaction))
            self.assertTrue(raid.is_test_member(interaction))
            await cog.on_signup(interaction, 9)
            self.assertIsInstance(
                interaction.followup.send.await_args.kwargs["view"],
                raid.ClassPickerView,
            )
            interaction.guild_id = 999
            self.assertFalse(raid.is_test_member(interaction))
            interaction.response.defer.reset_mock()
            interaction.response.send_message.reset_mock()
            await cog.on_signup(interaction, 9)
            interaction.response.send_message.assert_awaited_once()
            interaction.response.defer.assert_not_awaited()

    async def test_registration_modal_auto_signs_up_first_time(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "save_character") as save_character_mock, \
             patch.object(raid, "save_signup") as save_signup_mock, \
             patch.object(raid, "member_characters", return_value=[
                 (12, "Firstname Lastname", "Mage", "Frost", "DPS", None),
             ]):
            modal = raid.CharacterNameModal(42, "Mage", "Frost", "DPS", signup_raid_id=9)
            modal.name._value = "Firstname Lastname"
            modal.note._value = "Can stay for three hours"
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            await modal.on_submit(interaction)
            save_character_mock.assert_called_once_with(
                42, "Firstname Lastname", "Mage", "Frost", "DPS"
            )
            save_signup_mock.assert_called_once_with(
                9, 42, 12, note="Can stay for three hours"
            )
            self.assertIn("registered and signed up",
                          interaction.response.send_message.await_args.args[0])
            self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_character_lookup_is_ephemeral_in_any_guild_channel(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "character_list_text", return_value="Snore Mon"):
            cog = raid.RaidPrototype(SimpleNamespace())
            response = SimpleNamespace(send_message=AsyncMock())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
            )
            for channel_id in (raid.TEST_CHANNEL_ID, raid.RAID_CHANNEL_ID):
                interaction.channel_id = channel_id
                await cog.characters.callback(cog, interaction)
                self.assertTrue(response.send_message.await_args.kwargs["ephemeral"])
                self.assertIn("Snore Mon", response.send_message.await_args.args[0])
                response.send_message.reset_mock()

    async def test_loaded_but_public_channel_not_posted(self):
        source = (Path(__file__).resolve().parents[1] / "rollf.py").read_text()
        self.assertIn('await bot.load_extension("cogs.raid_signup")', source)
