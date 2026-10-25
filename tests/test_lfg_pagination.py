"""Verify one Discord message per LFM player, then embeds and footer."""

import unittest

from cogs.lfg_pagination import CLOSED_EMBED_SIZE, chunks, split_audience, page_specs


def open_plan(activity):
    return activity in ("Open / Flexible", "Group Questing (LFM)")


class BoardPaginationTests(unittest.TestCase):
    def test_thirty_open_players_each_get_own_message_plus_final_image(self):
        records = [{"user_id": i, "activity": "Open / Flexible"} for i in range(30)]
        looking, closed = split_audience(records, open_plan)
        specs = page_specs(looking, closed)
        self.assertEqual([kind for kind, _ in specs], ["entry"] * 30 + ["footer"])
        self.assertEqual(
            [row["user_id"] for kind, row in specs if kind == "entry"],
            list(range(30)),
        )
        self.assertIsNone(specs[-1][1])

    def test_mixed_players_have_separate_embeds_below_open_entries(self):
        records = [
            {"user_id": i, "activity": (
                "Open / Flexible" if i % 3 != 0 else "Full Group / Sorted"
            )}
            for i in range(35)
        ]
        open_rows, closed = split_audience(records, open_plan)
        specs = page_specs(open_rows, closed)
        self.assertEqual(
            [kind for kind, _ in specs],
            ["entry"] * len(open_rows) + ["closed"] * 2 + ["footer"],
        )
        self.assertEqual(len(closed), 12)
        self.assertTrue(all(len(group) <= CLOSED_EMBED_SIZE
                            for kind, group in specs if kind == "closed"))
        recovered = [row["user_id"] for kind, part in specs
                     for row in ([part] if kind == "entry"
                                 else part if kind == "closed" else [])]
        self.assertEqual(
            recovered,
            [row["user_id"] for row in open_rows + closed],
        )

    def test_empty_board_still_has_final_footer(self):
        self.assertEqual(page_specs([], []), [("footer", None)])
        rows = [{"activity": "Solo Questing"} for _ in range(2)]
        looking, closed = split_audience(rows, open_plan)
        self.assertEqual(
            page_specs(looking, closed), [("closed", rows), ("footer", None)],
        )

    def test_twenty_dummy_entries_can_be_shown_in_separate_messages(self):
        rows = [
            {"user_id": i, "activity": (
                "Open / Flexible" if i < 15 else "Solo Questing"
            )}
            for i in range(20)
        ]
        looking, closed = split_audience(rows, open_plan)
        specs = page_specs(looking, closed)
        self.assertEqual(len([kind for kind, _ in specs if kind == "entry"]), 15)
        self.assertEqual(sum(len(part) for kind, part in specs
                             if kind == "closed"), 5)
        self.assertEqual(specs[-1][0], "footer")


if __name__ == "__main__":
    unittest.main()
