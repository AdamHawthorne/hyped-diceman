"""Scrollable LFG board chunking: no click-through pages or hidden players."""

import unittest

from cogs.lfg_pagination import BOARD_CHUNK_SIZE, BOARD_TEXT_BUDGET, board_chunks
from cogs.lfg_sort import listing_board_key, is_open_lfm


def listing(index, activity, *, notes=""):
    return {
        "user_id": index,
        "character": f"Player{index:02d}",
        "activity": activity,
        "availability": f"{index % 23:02d}:00 UK - 23:00 UK",
        "days_off": "Thu 5 Nov",
        "notes": notes,
    }


class BoardScrollTests(unittest.TestCase):
    def test_thirty_people_are_all_visible_in_consecutive_chunks(self):
        roster = [listing(i, "Open / Flexible") for i in range(30)]
        chunks = board_chunks(roster)
        self.assertEqual(BOARD_CHUNK_SIZE, 7)
        self.assertGreaterEqual(len(chunks), 5)
        self.assertTrue(all(0 < len(chunk) <= 7 for chunk in chunks))
        self.assertEqual(
            [m["user_id"] for chunk in chunks for m in chunk],
            list(range(30)),
        )

    def test_solo_and_sorted_are_last_without_navigation(self):
        roster = [
            listing(1, "Full Group / Sorted"),
            listing(2, "Open / Flexible"),
            listing(3, "Solo Questing"),
            listing(4, "Group Questing (LFM)"),
            listing(5, "Grinding (Dungeons, grind spots etc)"),
        ]
        ordered = sorted(roster, key=listing_board_key)
        self.assertEqual([r["user_id"] for r in ordered][:2], [2, 4])
        self.assertTrue(all(not is_open_lfm(r["activity"]) for r in ordered[2:]))
        self.assertEqual(
            [r["user_id"] for chunk in board_chunks(ordered) for r in chunk],
            [r["user_id"] for r in ordered],
        )

    def test_variable_length_notes_split_before_text_budget(self):
        roster = [listing(i, "Open / Flexible", notes="a" * 240)
                  for i in range(24)]
        chunks = board_chunks(roster)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(len(chunk) for chunk in chunks), 24)
        for chunk in chunks:
            approx = 200 + sum(
                200 + sum(len(str(v)) for v in row.values()) for row in chunk
            )
            self.assertLessEqual(approx, BOARD_TEXT_BUDGET)

    def test_empty_board_keeps_one_anchor_message(self):
        self.assertEqual(board_chunks([]), [[]])

    def test_each_public_message_stays_within_discord_component_budget(self):
        # Heading container/section/text/thumbnail: 4 components.
        # One action row and five controls: 6 components.
        # Each profile container/section/text/thumbnail: 4 components.
        # Footer gallery + item: 2 components.
        self.assertLessEqual(4 + 6 + BOARD_CHUNK_SIZE * 4 + 2, 40)


if __name__ == "__main__":
    unittest.main()
