"""Offline tests of isolated raid sign-up preview and attendance schema."""
import json
import discord
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
            self.assertIn("y6bCxy.png", text)
            self.assertNotIn("XIszIH.png", text)
            self.assertIn("Roster Announcement:", text)
            self.assertIn("**Check:** <#1563663258047619123>", text)
            self.assertNotIn(" · <t:", raid_schedule.raid_panel_text(event))
            self.assertRegex(raid_schedule.raid_panel_text(event),
                             r"\*\*Date:\*\* <t:\d+:D>")
            self.assertRegex(raid_schedule.raid_panel_text(event),
                             r"\*\*Time:\*\* <t:\d+:t>")
            self.assertRegex(raid_schedule.raid_panel_text(event),
                             r"\*\*Roster Announcement:\*\* <t:\d+:R>")
            self.assertNotIn("Collecting gamers", text)
            self.assertIn("### Roster", text)
            self.assertIn("**Roster Announcement:**", text)
            panel_text = raid_schedule.raid_panel_text(event)
            self.assertLess(panel_text.index("**Check:**"),
                            panel_text.index("**Roster Announcement:**"))
            self.assertLess(panel_text.index("**Roster Announcement:**"),
                            panel_text.index("### Roster"))
            self.assertIn("**Selected players**", panel_text)
            self.assertIn("-# *Not announced*", panel_text)
            visible = raid_schedule.raid_panel_text(
                event, [("Snore Mon", "Ony, Hyjal"), ("Healer Main", "Barrows A")]
            )
            self.assertIn("• Snore Mon", visible)
            self.assertIn("• Healer Main", visible)
            self.assertLess(visible.index("Roster Announcement"), visible.index("Snore Mon"))
            self.assertNotIn("Applicant", visible)
            self.assertNotIn("Sign-ups hidden", text)
            self.assertNotIn("WGNGdr.png", text)
            self.assertNotIn("SoftRes sign-up", text)
            self.assertFalse(any(isinstance(c, raid_schedule.discord.ui.MediaGallery)
                                 for c in panel.children))
            # The banner belongs inside the same gold-bordered container,
            # after the roster text and before the independent button row.
            components = panel.to_components()
            self.assertEqual(len(components), 2)
            self.assertEqual(components[0]["type"], 17)  # Container
            self.assertEqual(components[1]["type"], 1)   # Action row of buttons
            self.assertEqual(components[0]["components"][0]["type"], 9)  # Section
            self.assertEqual(components[0]["components"][1]["type"], 12) # Media gallery
            self.assertIn("y6bCxy.png", json.dumps(components[0]["components"][1]))
            self.assertLess(text.index("Roster Announcement:"), text.index("y6bCxy.png"))
            self.assertLess(text.index("y6bCxy.png"), text.index('"label": "Sign"'))
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

    async def test_forever_roster_groups_show_separate_20_and_two_10_man_teams(self):
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
            for header in ("Hyjal · 20-player", "Barrows · 10-player A",
                           "Barrows · 10-player B", "Ony · 40-player"):
                self.assertIn("**" + header + "**", empty)
            self.assertEqual(empty.count("*Not announced*"), 4)
            self.assertIn("**Instances:** Hyjal (20m), Barrows (10m), Ony (40m)", empty)
            self.assertNotIn("**Raid:**", empty)
            self.assertLess(empty.index("**Roster Announcement:**"),
                            empty.index("### Roster"))

            # A character doing both Hyjal and Barrows appears on BOTH rosters.
            # Sheets only passes leadership-approved names to this method.
            members = [
                ("Snore Mon", "Hyjal, Barrows A, Ony"),
                ("Healer One", "Hyjal, Barrows B"),
                ("Healer Two", "Barrows B"),
                ("Pug Main", "Ony"),
                ("Gamer TBC", ""),
            ]
            grouped = raid_schedule.roster_groups(event, members)
            self.assertEqual(grouped, [
                ("Hyjal · 20-player", ["Snore Mon", "Healer One"]),
                ("Barrows · 10-player A", ["Snore Mon"]),
                ("Barrows · 10-player B", ["Healer One", "Healer Two"]),
                ("Ony · 40-player", ["Snore Mon", "Pug Main"]),
                ("Selected · group pending", ["Gamer TBC"]),
            ])
            text = raid_schedule.raid_panel_text(event, members)
            self.assertLess(text.index("**Hyjal · 20-player**"),
                            text.index("**Barrows · 10-player A**"))
            self.assertLess(text.index("**Barrows · 10-player A**"),
                            text.index("**Barrows · 10-player B**"))
            self.assertIn("**Selected · group pending**", text)
            self.assertEqual(text.count("• Snore Mon"), 3)
            self.assertNotIn("Signup", text)
            self.assertNotIn("Collecting gamers", text)

            # Barrows-only nights still display two distinct ten-player rosters.
            barrows_only = raid_schedule.save_raid(
                path, 2, "2026-12-10 20:00", "Barrows (10m)"
            )
            text = raid_schedule.raid_panel_text(
                barrows_only, [("Split Two", "Barrows B")]
            )
            self.assertIn("**Barrows · 10-player A**", text)
            self.assertIn("**Barrows · 10-player B**", text)
            self.assertNotIn("**Hyjal · 20-player**", text)
            self.assertNotIn("**Ony · 40-player**", text)

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
            self.assertNotIn("asI88C.png", components)
            self.assertNotIn("y6bCxy.png", components)
            self.assertIn("Choose raid date", components)
            self.assertIn("Choose start time", components)
            self.assertIn("Raid instance #1", components)
            self.assertIn("Raid instance #2", components)
            self.assertIn("Raid instance #3", components)
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
            self.assertIn("Edit Raid #1", json.dumps(
                raid_poster.RaidPosterView(dummy_cog, 42).to_components()
            ))

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
            self.assertIn('"label": "Sign"', components)
            self.assertIn('"style": 3', components)
            self.assertIn('"label": "Update"', components)
            self.assertIn('"label": "Gear"', components)
            self.assertIn('"label": "Absent"', components)
            self.assertNotIn('"label": "Register character"', components)
            self.assertNotIn('"label": "My characters"', components)
            self.assertNotIn("Signed up:", components)
            # Existing panels get active signup buttons when an on_signup is bound.
            buttons = [item for item in panel.walk_children()
                       if isinstance(item, discord.ui.Button)]
            self.assertFalse(next(b for b in buttons if b.label == "Sign").disabled)
            self.assertEqual([b.label for b in buttons],
                             ["Sign", "Update", "Gear", "Absent"])
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
                         instance_2=None, instance_3=None, softres_url=None)
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
            raid_schedule.record_panel(raid.RAID_DB, 1, raid.TEST_CHANNEL_ID, 7654)
            cog = raid.RaidPrototype(SimpleNamespace())
            posted = SimpleNamespace(delete=AsyncMock())
            poster_message = SimpleNamespace(edit=AsyncMock())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                channel=SimpleNamespace(fetch_message=AsyncMock(return_value=posted)),
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
        with tempfile.TemporaryDirectory() as tmp, patch.object(raid, "RAID_DB", str(Path(tmp) / "raid.db")), \
             patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            raid.create_schema(raid.RAID_DB)
            raid_schedule.ensure_raid_fields(raid.RAID_DB)
            cog = raid.RaidPrototype(SimpleNamespace())
            response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
            message = SimpleNamespace(id=7654, jump_url="https://discord.com/channels/77/55/7654", edit=AsyncMock())
            channel = SimpleNamespace(send=AsyncMock(return_value=message),
                                      fetch_message=AsyncMock(return_value=message))
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
                channel=channel, followup=SimpleNamespace(send=AsyncMock()),
            )
            await cog.raid_setup.callback(cog, interaction, 2, "2026-11-05 20:00", "Molten Core")
            response.defer.assert_awaited_once_with(ephemeral=True)
            kwargs = channel.send.await_args.kwargs
            self.assertIsInstance(kwargs["view"], raid_schedule.RaidEventPanel)
            self.assertTrue(kwargs["view"].is_persistent())
            self.assertEqual(raid_schedule.read_raid(raid.RAID_DB, 2)["panel_message_id"], 7654)
            self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
            await cog.raid_setup.callback(cog, interaction, 2, "2026-11-05 20:00", "Onyxia")
            channel.send.assert_awaited_once()
            message.edit.assert_awaited_once()
            self.assertIn("Onyxia", json.dumps(message.edit.await_args.kwargs["view"].to_components()))
            response.send_message.reset_mock()
            interaction.channel_id = raid.RAID_CHANNEL_ID
            await cog.raid_setup.callback(cog, interaction, 3, "2026-11-06 20:00", "Onyxia")
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
            self.assertIn("Pick character", data)
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
             ]), patch.object(raid, "my_signup", return_value=(
                 "Firstname Lastname", "Available",
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
            self.assertIsInstance(kwargs["view"], raid.CharacterActionView)
            self.assertTrue(kwargs["ephemeral"])
            self.assertNotIn("content", kwargs)
            self.assertEqual(followup.send.await_args.args, ())
            self.assertIn("**Now:** Firstname Lastname", json.dumps(kwargs["view"].to_components()))
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

    async def test_guild_member_can_signup_in_private_channel_but_not_post(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "read_raid", return_value={
                 "raid_id": 9, "signup_open": 1, "roster_published": 0,
             }), patch.object(raid, "member_characters", return_value=[]):
            cog = raid.RaidPrototype(SimpleNamespace())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=99), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
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
            interaction.channel_id = raid.RAID_CHANNEL_ID
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

    async def test_character_lookup_command_is_private(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            cog = raid.RaidPrototype(SimpleNamespace())
            response = SimpleNamespace(send_message=AsyncMock())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
            )
            with patch.object(raid, "character_list_text", return_value="Snore Mon"):
                await cog.characters.callback(cog, interaction)
            self.assertTrue(response.send_message.await_args.kwargs["ephemeral"])
            self.assertIn("Snore Mon", response.send_message.await_args.args[0])
            response.send_message.reset_mock()
            interaction.channel_id = raid.RAID_CHANNEL_ID
            await cog.characters.callback(cog, interaction)
            self.assertNotIn("Snore Mon", response.send_message.await_args.args[0])

    async def test_loaded_but_public_channel_not_posted(self):
        source = (Path(__file__).resolve().parents[1] / "rollf.py").read_text()
        self.assertIn('await bot.load_extension("cogs.raid_signup")', source)
