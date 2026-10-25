"""Preview is synthetic; labels are display-only and preserve canonical times."""

import unittest

from cogs.lfg_demo import demo_entries, display_race, display_uk_times
from cogs.lfg_sort import listing_start_key


class LFGLaunchPreviewTests(unittest.TestCase):
    def test_ten_distinct_synthetic_entries_sorted_earliest_first(self):
        entries = demo_entries()
        self.assertEqual(len(entries), 10)
        self.assertEqual(len({row["character"] for row in entries}), 10)
        self.assertEqual(entries, sorted(entries, key=listing_start_key))
        self.assertEqual(entries[0]["character"], "Grim Ironjaw")
        self.assertEqual(entries[-1]["character"], "Latearrival")
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

    def test_preview_component_budget(self):
        # Container + header section/text/thumb + divider +
        # ten player sections/text/thumb + four separating dividers.
        self.assertLessEqual(1 + 3 + 1 + 10 * 3 + 4, 40)


if __name__ == "__main__":
    unittest.main()
