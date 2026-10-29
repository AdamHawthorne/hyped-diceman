"""Copy-only checks for the Hyped Ogre gold coin Ledger panel."""
import json
import unittest

import discord
from cogs.roll_panels import CoinLeaderboard, PAGE_SIZE


class CoinLedgerPanelTests(unittest.TestCase):
    @staticmethod
    def render(rows, *, owner=123, page=0):
        panel = CoinLeaderboard(rows, owner_id=owner, coin_emoji="🪙", page=page)
        return panel, json.dumps(panel.to_components(), ensure_ascii=False)

    def test_ledger_has_short_singular_footer_and_one_requester_line(self):
        rows = [{"rank": 1, "user_id": 456, "coins": 1}]
        _, content = self.render(rows)
        self.assertIn("## Ogre\' Ledger", content)
        self.assertIn("Ogre give out 1 coin.", content)
        self.assertIn("<@123> asked who has most shiny.", content)
        self.assertIn("<@456>", content)
        self.assertNotIn("Hyped Gold Coins", content)
        self.assertNotIn("coins across the server", content)
        self.assertNotIn("Page 1 of 1", content)
        self.assertNotIn("asked...", content)
        self.assertNotIn("Who got most shiny things?", content)

    def test_plural_and_empty_totals(self):
        for count, expected in [(0, "0 coins"), (2, "2 coins"), (5, "5 coins")]:
            with self.subTest(count=count):
                rows = [{"rank": 1, "user_id": 456, "coins": count}] if count else []
                _, content = self.render(rows)
                self.assertIn("Ogre give out " + expected + ".", content)

    def test_multiple_pages_still_have_navigation_and_correct_lifetime_total(self):
        rows = [
            {"rank": i, "user_id": 1000 + i, "coins": 1}
            for i in range(1, PAGE_SIZE + 2)
        ]
        panel, content = self.render(rows, page=1)
        self.assertEqual(panel.page, 1)
        self.assertIn("Ogre give out 16 coins.", content)
        self.assertIn("‹ Previous", content)
        self.assertIn("Next ›", content)
        self.assertNotIn("Page 2 of 2", content)

    def test_original_ranking_and_coin_image_are_unchanged(self):
        rows = [{"rank": 1, "user_id": 456, "coins": 5}]
        _, content = self.render(rows)
        self.assertIn("**1.**", content)
        self.assertIn("**5**", content)
        self.assertIn("🪙", content)
