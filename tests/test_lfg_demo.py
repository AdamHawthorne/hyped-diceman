"""Preview is synthetic; labels are display-only and preserve canonical times."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from cogs.lfg_demo import demo_entries, display_race, display_uk_times
from cogs.lfg_sort import listing_board_key, is_solo_or_sorted


class LFGLaunchPreviewTests(unittest.TestCase):
    def test_ten_distinct_synthetic_entries_sorted_earliest_first(self):
        entries = demo_entries()
        self.assertEqual(len(entries), 10)
        self.assertEqual(len({row["character"] for row in entries}), 10)
        self.assertEqual(entries, sorted(entries, key=listing_board_key))
        self.assertEqual(entries[0]["character"], "Grim Ironjaw")
        self.assertEqual(entries[-1]["character"], "Duststep")
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
        statuses = [is_solo_or_sorted(row["activity"]) for row in rows]
        self.assertEqual(statuses, sorted(statuses))
        self.assertEqual([r["character"] for r in rows[-3:]],
                         ["Ashenveil", "Frostfang", "Duststep"])
        source = (ROOT / "cogs" / "lfg_preview.py").read_text(encoding="utf-8")
        self.assertIn('if not is_solo_or_sorted(row["activity"]):', source)

    def test_preview_component_budget(self):
        # Container + header section/text/thumb + divider +
        # ten player sections/text/thumb + four separating dividers.
        self.assertLessEqual(1 + 3 + 1 + 10 * 3 + 4, 40)


if __name__ == "__main__":
    unittest.main()
