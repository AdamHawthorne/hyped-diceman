"""Dungeon board, migration and shared-character regression tests."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("HYPED_GUILD_ID", "123456789012345678")

from cogs import lfg_dungeons as dungeon, lfg_preview as lfg
from cogs import raid_signup as raid


class DungeonRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lfg_path = str(Path(self.temp.name) / "lfg_board.db")
        self.char_path = str(Path(self.temp.name) / "raid_signup.db")
        patcher = patch.object(lfg, "PATH", self.lfg_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(raid, "RAID_DB", self.char_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        raid.create_schema(self.char_path)
        lfg.init_db()
        raid.save_character(123, "Snore", "Warrior", "Protection",
                            "Tank", self.char_path)
        self.character_id = raid.member_characters(123, self.char_path)[0][0]
        original_members = raid.member_characters
        original_race = raid.character_race
        patcher = patch.object(raid, "member_characters",
                               side_effect=lambda user, path=None: original_members(user, path or self.char_path))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(raid, "character_race",
                               side_effect=lambda user, cid, path=None: original_race(user, cid, path or self.char_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, user_id=123, dungeons=("Alcaz Prison", "Blackrock Depths"),
             wanted_items="Hand of Justice, a better shield",
             availability="Evenings 19:00 onwards"):
        with lfg.connect() as db:
            dungeon.save_request(
                db, 111, user_id, self.character_id, dungeons, wanted_items,
                availability, "Farm until drop", "https://i.vgy.me/XIszIH.png")

    def test_all_forever_new_endgame_dungeons_and_classic_45_plus(self):
        self.assertIn("Alcaz Prison", dungeon.DUNGEONS)
        self.assertIn("Blackmaw Hold", dungeon.DUNGEONS)
        self.assertIn("Shaper's Terrace", dungeon.DUNGEONS)
        self.assertIn("Zul'Farrak", dungeon.DUNGEONS)
        self.assertIn("Blackrock Depths", dungeon.DUNGEONS)
        self.assertIn("Scholomance", dungeon.DUNGEONS)
        self.assertLessEqual(len(dungeon.DUNGEONS), 25)
        self.assertEqual(dungeon.validate_dungeons(
            ("Blackmaw Hold", "Alcaz Prison", "Blackmaw Hold")),
            ("Alcaz Prison", "Blackmaw Hold"))
        with self.assertRaises(ValueError):
            dungeon.validate_dungeons(("Shadowfang Keep",))
        with self.assertRaises(ValueError):
            dungeon.validate_dungeons(())

    def test_items_are_plain_text_without_url_parsing(self):
        self.assertEqual(
            dungeon.normalise_wanted_items("  Hand of Justice,  staff  "),
            "Hand of Justice, staff",
        )
        self.assertEqual(
            dungeon.normalise_wanted_items("Need HoJ and a weapon"),
            "Need HoJ and a weapon",
        )
        with self.assertRaises(ValueError):
            dungeon.normalise_wanted_items("x" * 251)

    def test_existing_item_ids_are_preserved_as_plain_text(self):
        with lfg.connect() as db:
            db.execute(
                "INSERT INTO lfg_dungeon_requests "
                "(channel_id,user_id,character_id,dungeons,item_ids,availability) "
                "VALUES (111,123,?,'Blackrock Depths','11815,12590','Evenings')",
                (self.character_id,),
            )
            db.execute("UPDATE lfg_dungeon_requests SET wanted_items='11815, 12590'")
        lfg.init_db()
        with lfg.connect() as db:
            self.assertEqual(
                dungeon.get_request(db, 111, 123)["wanted_items"],
                "11815, 12590",
            )
        self.save(wanted_items="Justice, shield")
        lfg.init_db()
        with lfg.connect() as db:
            self.assertEqual(
                dungeon.get_request(db, 111, 123)["wanted_items"],
                "Justice, shield",
            )
            self.assertEqual(dungeon.get_request(db, 111, 123)["item_ids"], "")

    def test_dungeon_request_updates_remove_without_touching_launch_plan(self):
        with lfg.connect() as db:
            db.execute(
                "INSERT INTO lfg_listings "
                "(channel_id,user_id,character,class_name,role,availability,activity) "
                "VALUES (111,123,'Legacy Name','Warrior','Tank','Later','Open / Flexible')")
        self.save()
        self.save(dungeons=("Shaper's Terrace",), wanted_items="New staff",
                  availability="Sunday afternoon")
        with lfg.connect() as db:
            requests = dungeon.list_requests(db, 111)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["character_id"], self.character_id)
            self.assertEqual(requests[0]["dungeons"], "Shaper's Terrace")
            self.assertEqual(requests[0]["wanted_items"], "New staff")
            self.assertEqual(dungeon.delete_request(db, 111, 999), 0)
            self.assertEqual(dungeon.delete_request(db, 111, 123), 1)
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM lfg_listings WHERE user_id=123").fetchone())

    def test_board_uses_latest_canonical_character_and_plain_text_items(self):
        self.save()
        board = lfg.BoardLayout(SimpleNamespace(), 111, guild=None)
        payload = json.dumps(board.to_components())
        self.assertIn("LF Dungeons", payload)
        self.assertIn("Characters", payload)
        self.assertIn("Add / Update", payload)
        self.assertIn("Alcaz Prison", payload)
        self.assertIn("Blackrock Depths", payload)
        self.assertIn("Hand of Justice, a better shield", payload)
        self.assertNotIn("wowhead.com", payload)
        self.assertIn("Remove Dungeon", payload)
        self.assertIn("Evenings 19:00 onwards", payload)
        self.assertIn("Snore", payload)
        self.assertIn("Warrior", payload)
        self.assertIn("Tank", payload)
        raid.update_character_profile(
            123, self.character_id, "Warrior", "Fury", "DPS", self.char_path)
        updated = json.dumps(
            lfg.BoardLayout(SimpleNamespace(), 111, guild=None).to_components())
        self.assertIn("DPS", updated)

    def test_legacy_launch_rows_survive_additive_migration(self):
        with lfg.connect() as db:
            db.execute(
                "INSERT INTO lfg_listings "
                "(channel_id,user_id,character,class_name,role,availability,activity,race) "
                "VALUES (111,456,'Old Char','Mage','DPS','Later','Open / Flexible','Troll')")
        lfg.init_db()
        lfg.init_db()
        with lfg.connect() as db:
            old = db.execute(
                "SELECT character,race,activity,character_id FROM lfg_listings "
                "WHERE channel_id=111 AND user_id=456").fetchone()
            self.assertEqual(tuple(old), ("Old Char", "Troll", "Open / Flexible", None))
            self.assertEqual(dungeon.list_requests(db, 111), [])

    def test_shared_race_validation_and_no_duplicate_characters(self):
        raid.set_character_race(123, self.character_id, "Orc", self.char_path)
        self.assertEqual(raid.character_race(123, self.character_id, self.char_path),
                         "Orc")
        raid.update_character_profile(
            123, self.character_id, "Mage", "Frost", "DPS", self.char_path)
        with self.assertRaises(ValueError):
            raid.set_character_race(123, self.character_id, "Tauren", self.char_path)
        self.assertEqual(raid.character_race(123, self.character_id, self.char_path), "Orc")
        self.assertEqual(len(raid.member_characters(123, self.char_path)), 1)


if __name__ == "__main__":
    unittest.main()
