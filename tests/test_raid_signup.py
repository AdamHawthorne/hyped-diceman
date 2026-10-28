"""Offline tests of isolated raid sign-up preview and attendance schema."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import cogs.raid_signup as raid


class RaidPrototypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_attendance_fields_and_status_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with raid.database(path) as con:
                tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"characters", "raids", "signups", "gear_exports", "attendance"} <= tables)
                fields = {row[1] for row in con.execute("PRAGMA table_info(attendance)")}
                self.assertTrue({"raid_id", "discord_id", "character_id", "status", "late_minutes", "afk_minutes", "leadership_notes"} <= fields)
        self.assertEqual(set(raid.ATTENDANCE_OPTIONS), {"Pending", "Attended", "Late", "AFK", "No-show", "Excused"})
        self.assertIn("Attendance", raid.SHEET_TABS)

    async def test_private_preview_only_and_disabled_buttons(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            cog = raid.RaidPrototype(SimpleNamespace())
            response = SimpleNamespace(send_message=AsyncMock())
            interaction = SimpleNamespace(user=SimpleNamespace(id=42), guild_id=77,
                                          channel_id=raid.TEST_CHANNEL_ID, response=response)
            await cog.preview.callback(cog, interaction)
            kwargs = response.send_message.await_args.kwargs
            self.assertTrue(kwargs["ephemeral"])
            data = json.dumps(kwargs["view"].to_components())
            self.assertIn("Raid sign-up:", data)
            self.assertIn('"disabled": true', data)
            response.send_message.reset_mock()
            interaction.channel_id = raid.RAID_CHANNEL_ID
            await cog.preview.callback(cog, interaction)
            self.assertNotIn("view", response.send_message.await_args.kwargs)

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
