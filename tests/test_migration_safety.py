"""Offline regression guards for the Hyped Cog split.

Run: python -m unittest discover -s tests -v
No Discord token, network or persistent database required.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MigrationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "rollf.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.main_functions = {
            node.name for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_critical_dice_functions_unchanged_in_location(self):
        for func in ("insert_roll", "calculate_streaks", "get_period_stats",
                     "bot_daily_roll", "perform_roll", "sync_high_roller_role"):
            with self.subTest(func=func):
                self.assertIn(func, self.main_functions)

    def test_existing_database_schema_still_present(self):
        for sql in ("CREATE TABLE IF NOT EXISTS rolls",
                    "CREATE TABLE IF NOT EXISTS flameboard_posts"):
            self.assertIn(sql, self.source)

    def test_forever_button_id_and_messages_preserved(self):
        source = (ROOT / "cogs/forever_role.py").read_text(encoding="utf-8")
        self.assertIn("hyped_ogre_forever_role", source)
        self.assertIn("**Forever** role added.", source)
        self.assertIn("**Forever** role removed.", source)

    def test_flameboard_deduplicated_event_listeners(self):
        source = (ROOT / "cogs/flameboard.py").read_text(encoding="utf-8")
        self.assertIn("on_raw_reaction_add", source)
        self.assertIn("on_raw_reaction_remove", source)
        self.assertNotIn("async def on_raw_reaction_add", self.source)
        self.assertNotIn("async def on_raw_reaction_remove", self.source)

    def test_cogs_loaded_on_start(self):
        self.assertIn('bot.load_extension("cogs.lfg_preview")', self.source)
        self.assertIn("FlameboardEvents(bot, HYPED_GUILD_ID, sync_flameboard_message)", self.source)

    def test_lfg_is_isolated_from_legacy_data(self):
        source = (ROOT / "cogs/lfg_preview.py").read_text(encoding="utf-8")
        self.assertIn("lfg_board.db", source)
        self.assertIn("class BoardLayout", source)
        self.assertIn("class WizardState", source)
        self.assertIn("class PlannedNameModal", source)
        self.assertIn("class RaceView", source)
        self.assertIn("class RoleSelect", source)
        self.assertIn("class ReviewView", source)
        self.assertIn("get_partial_message", source)
        self.assertIn("ORDER BY character COLLATE NOCASE ASC", source)
        self.assertIn("<Hyped> LFG-LFM Board", source)
        self.assertIn("discord.ui.LayoutView", source)
        self.assertIn("discord.ui.Separator", source)
        self.assertIn("discord.ui.Section", source)
        self.assertIn("https://i.vgy.me/2Jt4oi.png", source)
        self.assertIn("Skyborne (Windshaper)", source)
        self.assertIn("STARTING_ZONES", source)
        self.assertIn("DAYS_OFF", source)
        self.assertIn("CLASS_ROLES", source)
        self.assertIn("class AvailabilityModal", source)
        self.assertIn("Group Questing + Dungeons", source)
        self.assertIn("Grinding (Dungeons, grind spots etc)", source)
        self.assertIn("Ogre want know launch plans.", source)
        self.assertIn("Remember last name ;)", source)
        self.assertIn("START_TIMES", source)
        self.assertIn("FINISH_TIMES", source)
        self.assertIn("LAUNCH_DAYS", source)
        self.assertIn("Launch Night · Wed 4 Nov", source)
        self.assertIn("Solo Questing + Dungeons", source)
        self.assertIn("Flexible", source)
        self.assertIn("class NotesModal", source)
        self.assertIn("-# Playing:", source)
        self.assertIn("discord.ui.MediaGallery", source)
        self.assertIn("https://i.vgy.me/WGNGdr.png", source)
        self.assertIn("style=discord.ButtonStyle.secondary", source)
        self.assertIn("display_avatar.url", source)
        self.assertIn('name="lfg-preview"', source)
        self.assertNotIn("DROP TABLE", source)
        self.assertNotIn("DELETE FROM rolls", source)

    def test_no_alter_existing_dice_tables(self):
        self.assertNotIn("DROP TABLE", self.source.upper())
        self.assertNotIn("DELETE FROM ROLLS", self.source.upper())


if __name__ == "__main__":
    unittest.main()
