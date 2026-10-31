"""Preview is synthetic; labels are display-only and preserve canonical times."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from cogs.lfg_demo import demo_entries, display_race, display_uk_times
from cogs.lfg_sort import listing_board_key, is_solo_or_sorted, is_open_lfm


class LFGLaunchPreviewTests(unittest.TestCase):
    def test_twenty_distinct_synthetic_entries_sorted_earliest_first(self):
        entries = demo_entries()
        self.assertEqual(len(entries), 20)
        self.assertEqual(len({row["character"] for row in entries}), 20)
        self.assertEqual(entries, sorted(entries, key=listing_board_key))
        self.assertEqual(entries[0]["character"], "Grim Ironjaw")
        self.assertEqual(entries[-1]["character"], "Stormcaller")
        self.assertEqual(entries[0]["availability"], "Launch 23:00 UK - 04:00 UK")

    def test_demo_has_a_mix_of_open_solo_sorted_and_lfm(self):
        plans = [row["activity"] for row in demo_entries()]
        self.assertIn("Open / Flexible", plans)
        self.assertIn("Full Group / Sorted", plans)
        self.assertIn("Solo Questing", plans)
        self.assertIn("Group Questing (LFM)", plans)
        self.assertIn("Solo Questing + Dungeons (LFG/ Pugging)", plans)

    def test_compact_time_display_is_read_only(self):
        self.assertEqual(display_uk_times("01:00 UK - 13:00 UK"), "1am UK - 1pm UK")
        self.assertEqual(display_uk_times("00:00 UK - 12:00 UK"), "12am UK - 12pm UK")
        self.assertEqual(display_uk_times("Launch 23:00 UK + 24 hours"),
                         "Launch 11pm UK + 24 hours")
        self.assertEqual(display_uk_times("Later - 22:00 UK"), "Later - 10pm UK")

    def test_skyborne_board_label_only(self):
        self.assertEqual(display_race("Skyborne (Windshaper)"), "Skyborne")
        self.assertEqual(display_race("Undead"), "Undead")
        self.assertTrue(any(row["race"] == "Skyborne (Windshaper)" for row in demo_entries()))

    def test_solo_and_sorted_at_bottom_with_times_hidden_in_board(self):
        rows = demo_entries()
        non_lfm = [not is_open_lfm(row["activity"]) for row in rows]
        self.assertEqual(non_lfm, sorted(non_lfm))
        self.assertTrue(all(not is_open_lfm(row["activity"]) for row in rows[-5:]))
        source = (ROOT / "cogs" / "lfg_preview.py").read_text(encoding="utf-8")
        self.assertIn('if not is_solo_or_sorted(row["activity"]):', source)

    def test_preview_edits_only_one_ephemeral_message(self):
        source = (ROOT / "cogs" / "lfg_preview.py").read_text(encoding="utf-8")
        self.assertNotIn("class BoardPageButton", source)
        self.assertIn("class BoardLayout", source)
        self.assertIn("lfg_board_overflow", source)
        self.assertIn("await interaction.response.edit_message(", source)
        self.assertIn("view=BoardLayout(self, interaction.channel_id,", source)
        self.assertIn("preview=True", source)
        self.assertNotIn("await interaction.followup.send(view=FooterLayout()", source)
        self.assertNotIn("gallery.add_item(media=BOTTOM_IMAGE", source)
        self.assertNotIn("BOTTOM_IMAGE", source)
        self.assertIn('if not is_solo_or_sorted(row["activity"]):', source)


if __name__ == "__main__":
    unittest.main()
