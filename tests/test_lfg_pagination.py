"""Ensure 30+ real listings fit consecutive V2 messages with no omissions."""

import unittest

from cogs.lfg_pagination import (
    OPEN_PAGE_SIZE, CLOSED_EMBED_SIZE, chunks, split_audience, page_specs,
)


def open_plan(activity):
    return activity == "Open / Flexible" or activity == "Group Questing (LFM)"


class BoardPaginationTests(unittest.TestCase):
    def test_thirty_open_entries_have_six_continuous_five_player_pages(self):
        records = [{"user_id": i, "activity": "Open / Flexible"} for i in range(30)]
        open_rows, closed = split_audience(records, open_plan)
        pages = chunks(open_rows, OPEN_PAGE_SIZE)
        self.assertEqual(len(pages), 6)
        self.assertTrue(all(len(page) == 5 for page in pages))
        self.assertEqual([row["user_id"] for page in pages for row in page],
                         list(range(30)))
        additional = page_specs(open_rows, closed)
        self.assertEqual([kind for kind, _ in additional], ["v2"] * 5)

    def test_mixed_group_and_non_group_entries_get_separate_embeds(self):
        records = [
            {"user_id": i, "activity": (
                "Open / Flexible" if i % 3 != 0 else "Full Group / Sorted"
            )}
            for i in range(35)
        ]
        looking, not_looking = split_audience(records, open_plan)
        expected = ([r["user_id"] for r in looking]
                    + [r["user_id"] for r in not_looking])
        parts = page_specs(looking, not_looking)
        actual = (
            [r["user_id"] for r in looking[:OPEN_PAGE_SIZE]]
            + [r["user_id"] for _, part in parts for r in part]
        )
        self.assertEqual(actual, expected)
        self.assertEqual(len(not_looking), 12)
        self.assertEqual(len([kind for kind, _ in parts if kind == "closed"]), 2)
        self.assertTrue(all(len(page) <= CLOSED_EMBED_SIZE
                            for kind, page in parts if kind == "closed"))
        self.assertEqual([kind for kind, _ in parts],
                         sorted([kind for kind, _ in parts],
                                key=lambda kind: kind != "v2"))

    def test_zero_looking_still_renders_an_anchor_plus_embed(self):
        records = [{"activity": "Solo Questing"} for _ in range(2)]
        looking, other = split_audience(records, open_plan)
        self.assertEqual(looking, [])
        self.assertEqual(page_specs(looking, other), [("closed", records)])

    def test_twenty_preview_entries_are_not_truncated(self):
        records = [
            {"user_id": i, "activity": (
                "Open / Flexible" if i < 15 else "Solo Questing"
            )}
            for i in range(20)
        ]
        looking, closed = split_audience(records, open_plan)
        pieces = chunks(looking, OPEN_PAGE_SIZE) + chunks(closed, CLOSED_EMBED_SIZE)
        self.assertEqual(sum(map(len, pieces)), 20)
        self.assertEqual(len(pieces), 4)


if __name__ == "__main__":
    unittest.main()
