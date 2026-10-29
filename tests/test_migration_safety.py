"""Offline regression guards for the Hyped Cog split.

Run: python -m unittest discover -s tests -v
No Discord token, network or persistent database required.
"""
import ast
import re
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

    def test_flameboard_has_inline_jump_link_and_live_reaction_count(self):
        self.assertNotIn('Cm1ZTF.png', self.source)
        self.assertIn('f"[Jump to message]({message.jump_url})"', self.source)
        self.assertIn('reaction_counts.get(emoji, 0)', self.source)
        self.assertIn('await board_message.edit(content=header, embed=embed, view=None)', self.source)
        self.assertIn('for emoji in ("🔥", "😂", "⭐") if reaction_counts.get(emoji, 0) > 0', self.source)
        self.assertIn('return f"{reactions}  <#{message.channel.id}>"', self.source)
        self.assertNotIn('flameboard_jump_view(message.jump_url)', self.source)
        self.assertIn('file=media_file,', self.source)

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
        self.assertIn("sorted(roster, key=listing_board_key)", source)
        self.assertIn("from .lfg_sort import listing_board_key, is_solo_or_sorted", source)
        self.assertIn("<Hyped> LFG-LFM Board", source)
        self.assertIn("discord.ui.LayoutView", source)
        self.assertNotIn("discord.ui.Separator", source)
        self.assertIn("discord.ui.Section", source)
        self.assertIn("https://i.vgy.me/2Jt4oi.png", source)
        self.assertIn("Skyborne (Windshaper)", source)
        self.assertIn("STARTING_ZONES", source)
        self.assertIn("CLASS_ROLES", source)
        self.assertIn("class StartTimeView", source)
        self.assertIn("class FinishTimeView", source)
        self.assertIn("class LaunchDaysView", source)
        self.assertIn("Group Questing + Dungeons (LFM)", source)
        self.assertIn("Grinding (Dungeons, grind spots etc)", source)
        self.assertIn("Ogre want know launch plans.", source)
        self.assertIn('label="Character name"', source)
        self.assertIn('placeholder="Forename Surname"', source)
        self.assertIn("START_TIMES", source)
        self.assertIn("FINISH_TIMES", source)
        self.assertIn("LAUNCH_DAYS", source)
        self.assertIn("Launch Night · Wed 4 Nov", source)
        self.assertIn("Solo Questing + Dungeons (LFG/ Pugging)", source)
        self.assertIn("Open / Flexible", source)
        self.assertIn("class NotesModal", source)
        self.assertIn("**Starting Zone:**", source)
        self.assertIn("**Playing:**", source)
        self.assertIn("**Notes:**", source)
        self.assertIn("**Plan:**", source)
        self.assertIn("WGNGdr.png", source)
        self.assertIn("compact_days", source)
        self.assertIn("Sun 15 Nov", source)
        self.assertIn("50 hours +", source)
        self.assertIn("style=discord.ButtonStyle.success", source)
        self.assertIn("style=discord.ButtonStyle.secondary", source)
        self.assertIn("display_avatar.url", source)
        self.assertIn('name="lfg-preview"', source)
        self.assertIn('name="postlfglfm"', source)
        self.assertIn("preview=True", source)
        self.assertIn("CREATE TABLE IF NOT EXISTS lfg_board_parts", source)
        self.assertIn("from .lfg_pagination import PAGE_SIZE, page_count, clamp_page, page_rows", source)
        self.assertIn("class BoardPageButton", source)
        self.assertIn("class BoardLayout", source)
        self.assertNotIn("not_lfm_embed", source)
        self.assertNotIn("class EntryLayout", source)
        self.assertNotIn("class FooterLayout", source)
        self.assertIn("ALTER TABLE lfg_boards ADD COLUMN page_index", source)
        self.assertIn("DELETE FROM lfg_board_parts WHERE channel_id=? AND message_id=?", source)
        self.assertIn("await anchor.edit(", source)
        self.assertIn("await interaction.response.edit_message(", source)
        self.assertIn("notes=excluded.notes", source)
        self.assertIn("await self.refresh(channel.id)", source)
        self.assertIn('if not is_solo_or_sorted(row["activity"]):', source)
        self.assertNotIn("DROP TABLE", source)
        self.assertNotIn("DELETE FROM rolls", source)

    def test_lfg_levelling_plan_options(self):
        source = (ROOT / "cogs/lfg_preview.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        options = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "INTENTIONS"
        )
        self.assertEqual(options[0], "Open / Flexible")
        self.assertEqual(options.count("Open / Flexible"), 1)
        self.assertIn("Group Questing (LFM)", options)
        self.assertIn("Group Questing + Dungeons (LFM)", options)
        self.assertEqual(options[-1], "Full Group / Sorted")
        self.assertNotIn("Flexible", options)
        self.assertLessEqual(len(options), 25)

    def test_lfg_select_menus_stay_within_discord_limit(self):
        source = (ROOT / "cogs/lfg_preview.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in {"START_TIMES", "FINISH_TIMES", "LONG_SESSION_TIMES", "LAUNCH_DAYS"}:
                    values[node.targets[0].id] = ast.literal_eval(node.value)

        for name, options in values.items():
            with self.subTest(name=name):
                self.assertLessEqual(len(options), 25)

    def test_no_alter_existing_dice_tables(self):
        self.assertNotIn("DROP TABLE", self.source.upper())
        self.assertNotIn("DELETE FROM ROLLS", self.source.upper())

    def test_roll_history_is_append_only_across_runtime_sources(self):
        runtime_sources = [ROOT / "rollf.py", *(ROOT / "cogs").glob("*.py")]
        forbidden = (
            "DELETE FROM ROLLS",
            "DROP TABLE ROLLS",
            "REPLACE INTO ROLLS",
            "TRUNCATE TABLE ROLLS",
        )
        for path in runtime_sources:
            source = path.read_text(encoding="utf-8").upper()
            for sql in forbidden:
                with self.subTest(path=path.name, sql=sql):
                    self.assertNotIn(sql, source)

            # The only historic UPDATE allowed on the canonical table is the
            # additive roll_date backfill; roll values/timestamps/owners stay immutable.
            for match in re.finditer(r"UPDATE\s+ROLLS\s+SET\s+([^\n]+)", source):
                with self.subTest(path=path.name, update=match.group(0)):
                    self.assertTrue(match.group(1).lstrip().startswith("ROLL_DATE"))

    def test_feature_databases_are_explicitly_persisted_on_fly_volume(self):
        fly = (ROOT / "fly.toml").read_text(encoding="utf-8")
        self.assertIn('destination = "/data"', fly)
        for path in (
            "/data/bot.db",
            "/data/raid_signup.db",
            "/data/lfg_board.db",
            "/data/countdown.db",
            "/data/streams.db",
        ):
            with self.subTest(path=path):
                self.assertIn(path, fly)

    def test_raid_and_feature_databases_never_reuse_bot_db(self):
        for relative in (
            "cogs/raid_signup.py",
            "cogs/lfg_preview.py",
            "cogs/countdown.py",
            "cogs/streams.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(source=relative):
                self.assertNotIn('"/data/bot.db"', source)


if __name__ == "__main__":
    unittest.main()
