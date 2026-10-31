"""Dungeon board, migration and shared-character regression tests."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    def test_all_classic_and_forever_dungeons_in_one_level_ordered_list(self):
        self.assertEqual(len(dungeon.DUNGEONS), 35)
        self.assertEqual(len(set(dungeon.DUNGEONS)), 35)
        self.assertEqual(len(dungeon.DUNGEON_LEVELS), 35)
        for name in (
            "Ragefire Chasm", "Wailing Caverns", "The Deadmines",
            "Shadowfang Keep", "Scarlet Monastery Graveyard",
            "Scarlet Monastery Library", "Scarlet Monastery Armory",
            "Scarlet Monastery Cathedral", "Zul'Farrak", "Maraudon",
            "Blackrock Depths", "Scholomance", "Dire Maul East",
            "Dire Maul West", "Dire Maul North", "Stratholme Live",
            "Stratholme Undead",
            "Hall of Thanes", "Ruins of Lordaeron", "Excavation Site",
            "City of Dalaran", "The Drowned City", "Krol'dok Stronghold",
            "Alcaz Prison", "Blackmaw Hold", "Shaper's Terrace",
        ):
            with self.subTest(name=name):
                self.assertIn(name, dungeon.DUNGEONS)
        for pvp in (
            "Warsong Gulch", "Arathi Basin", "Alterac Valley",
            "World PvP", "Battleground Premades",
        ):
            self.assertNotIn(pvp, dungeon.DUNGEONS)
            with self.assertRaises(ValueError):
                dungeon.validate_dungeons((pvp,))
        self.assertEqual(
            dungeon.validate_dungeons((
                "Blackrock Depths", "Hall of Thanes", "Shaper's Terrace")),
            ("Hall of Thanes", "Blackrock Depths", "Shaper's Terrace")
        )
        self.assertEqual(
            dungeon.validate_dungeons((
                "Blackmaw Hold", "Alcaz Prison", "Blackmaw Hold")),
            ("Alcaz Prison", "Blackmaw Hold")
        )
        with self.assertRaises(ValueError):
            dungeon.validate_dungeons(("Shadowfang Cavern",))
        with self.assertRaises(ValueError):
            dungeon.validate_dungeons(())

    def test_flat_dungeon_dropdowns_fit_discord_limit(self):
        chunks = dungeon.DUNGEON_SELECT_GROUPS
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(1 <= len(group) <= 25 for group in chunks))
        self.assertEqual(
            tuple(name for group in chunks for name in group),
            dungeon.DUNGEONS
        )

    def test_selection_view_shows_all_dungeons_without_category_picker(self):
        state = SimpleNamespace(
            dungeons=("Hall of Thanes", "Blackrock Depths"),
            check=AsyncMock(return_value=True),
        )
        view = lfg.DungeonSelectionView(state)
        selects = [
            child for child in view.children
            if isinstance(child, lfg.discord.ui.Select)
        ]
        self.assertEqual(len(selects), 2)
        self.assertEqual(
            [len(select.options) for select in selects],
            [len(group) for group in dungeon.DUNGEON_SELECT_GROUPS]
        )
        self.assertEqual(
            [option.value for select in selects for option in select.options],
            list(dungeon.DUNGEONS)
        )
        self.assertTrue(all(
            option.description.startswith("Level ")
            for select in selects for option in select.options
        ))
        self.assertEqual(
            {option.value for select in selects for option in select.options
             if option.default},
            {"Hall of Thanes", "Blackrock Depths"},
        )
        view_text = json.dumps(view.to_components())
        self.assertNotIn("PvP", view_text)
        self.assertNotIn("Choose a category", view_text)
        self.assertIn("Selected dungeons (2)", lfg.dungeon_selection_summary(state))
        buttons = [
            child for child in view.children
            if isinstance(child, lfg.discord.ui.Button)
        ]
        self.assertEqual([button.label for button in buttons], ["Back", "Next"])
        self.assertFalse(buttons[1].disabled)

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


class DungeonDropdownInteractions(unittest.IsolatedAsyncioTestCase):
    async def test_both_flat_selects_merge_choices_and_deselections(self):
        state = SimpleNamespace(
            dungeons=("Hall of Thanes",),
            check=AsyncMock(return_value=True),
        )
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock())
        )

        first_view = lfg.DungeonSelectionView(state)
        first = next(
            child for child in first_view.children
            if isinstance(child, lfg.discord.ui.Select)
        )
        first._values = ["Hall of Thanes", "Ragefire Chasm"]
        await first.callback(interaction)
        self.assertEqual(
            state.dungeons, ("Ragefire Chasm", "Hall of Thanes")
        )

        second_view = interaction.response.edit_message.await_args.kwargs["view"]
        second = [
            child for child in second_view.children
            if isinstance(child, lfg.discord.ui.Select)
        ][1]
        second._values = ["Shaper's Terrace", "Blackrock Depths"]
        await second.callback(interaction)
        self.assertEqual(
            state.dungeons,
            ("Ragefire Chasm", "Hall of Thanes", "Blackrock Depths",
             "Shaper's Terrace"),
        )

        updated_view = interaction.response.edit_message.await_args.kwargs["view"]
        first = [
            child for child in updated_view.children
            if isinstance(child, lfg.discord.ui.Select)
        ][0]
        first._values = []
        await first.callback(interaction)
        self.assertEqual(
            state.dungeons, ("Blackrock Depths", "Shaper's Terrace")
        )
        self.assertNotIn("PvP", json.dumps(
            interaction.response.edit_message.await_args.kwargs["view"].to_components()
        ))

    async def test_new_request_next_disabled_until_dungeon_selected(self):
        state = SimpleNamespace(dungeons=(), check=AsyncMock(return_value=True))
        view = lfg.DungeonSelectionView(state)
        self.assertTrue(next(
            child.disabled for child in view.children
            if isinstance(child, lfg.discord.ui.Button) and child.label == "Next"
        ))
        self.assertIn("Choose any dungeons", lfg.dungeon_selection_summary(state))


if __name__ == "__main__":
    unittest.main()
