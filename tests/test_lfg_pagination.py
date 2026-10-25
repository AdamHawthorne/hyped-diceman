"""One V2 message; navigation shows everyone without extra Discord posts."""

import unittest

from cogs.lfg_pagination import PAGE_SIZE, clamp_page, page_count, page_rows
from cogs.lfg_sort import listing_board_key


def listing(index, activity):
    return {
        "user_id": index,
        "character": f"Player{index:02d}",
        "activity": activity,
        "availability": f"{index % 23:02d}:00 UK - 23:00 UK",
        "days_off": "Thu 5 Nov",
    }


class BoardPaginationTests(unittest.TestCase):
    def test_thirty_people_are_all_available_with_eight_pages(self):
        roster = [listing(i, "Open / Flexible") for i in range(30)]
        self.assertEqual(PAGE_SIZE, 4)
        self.assertEqual(page_count(len(roster)), 8)
        pages = [page_rows(roster, p) for p in range(page_count(len(roster)))]
        self.assertEqual([len(page) for page in pages], [4] * 7 + [2])
        self.assertEqual(
            [member["user_id"] for page in pages for member in page],
            list(range(30)),
        )

    def test_solo_sorted_and_other_plans_are_last_without_separate_embeds(self):
        roster = [
            listing(1, "Full Group / Sorted"),
            listing(2, "Open / Flexible"),
            listing(3, "Solo Questing"),
            listing(4, "Group Questing (LFM)"),
            listing(5, "Grinding (Dungeons, grind spots etc)"),
        ]
        ordered = sorted(roster, key=listing_board_key)
        self.assertEqual([r["user_id"] for r in ordered][-2:], [1, 3])
        self.assertEqual(
            [r["user_id"] for p in range(page_count(len(ordered)))
             for r in page_rows(ordered, p)],
            [r["user_id"] for r in ordered],
        )

    def test_shrinking_roster_clamps_current_page(self):
        self.assertEqual(clamp_page(7, 30), 7)
        self.assertEqual(clamp_page(7, 5), 1)
        self.assertEqual(clamp_page(-1, 5), 0)
        self.assertEqual(clamp_page(300, 0), 0)
        self.assertEqual(page_count(0), 1)

    def test_twenty_preview_entries_have_five_pages(self):
        roster = [listing(i, "Open / Flexible") for i in range(20)]
        self.assertEqual(page_count(len(roster)), 5)
        self.assertEqual(
            sum(len(page_rows(roster, i)) for i in range(5)), 20,
        )

    def test_maximum_component_budget_with_four_individual_containers(self):
        # Header container/section/text/thumbnail = 4
        # Editing row + four buttons = 5; page row + two buttons = 3
        # Page text = 1; individual player container/section/text/avatar/rule = 5
        # Footer gallery + media item = 2 (last page only).
        self.assertLessEqual(4 + 5 + 3 + 1 + PAGE_SIZE * 5 + 2, 40)
        # Top-level: header, two rows, page label, four member containers,
        # and footer gallery on last page.
        self.assertLessEqual(1 + 2 + 1 + PAGE_SIZE + 1, 10)


if __name__ == "__main__":
    unittest.main()
