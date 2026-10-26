"""Checks for the Warcraft-inspired voice bank."""
import unittest
from cogs.ogre_voice import PHRASES, pick, count_label


class OgreVoiceTests(unittest.TestCase):
    def test_large_original_phrase_library(self):
        self.assertGreaterEqual(len(PHRASES), 12)
        self.assertGreaterEqual(sum(map(len, PHRASES.values())), 150)
        self.assertTrue(all(len(set(lines)) == len(lines) for lines in PHRASES.values()))

    def test_seeded_selection_is_consistent(self):
        a = pick("daily_prompt", seed="2026-10-26")
        self.assertEqual(a, pick("daily_prompt", seed="2026-10-26"))
        self.assertIn(a, PHRASES["daily_prompt"])

    def test_template_substitutes_winner_details(self):
        text = pick("coin_winner", seed="2026-10-26", name="@Tom",
                    day="Monday", roll=92, coin="OGRE_COIN")
        for fragment in ("@Tom", "Monday", "92", "OGRE_COIN"):
            self.assertIn(fragment, text)

    def test_bot_winning_has_ogre_first_person(self):
        line = pick("coin_winner_ogre", seed="2026-10-26",
                    name="@Ogre", day="Monday", roll=100, coin="OGRE_COIN")
        self.assertIn("Monday", line)
        self.assertIn("100", line)

    def test_each_feature_has_safe_voice(self):
        categories = (
            "daily_prompt", "coin_winner", "coin_winner_ogre",
            "roll_duplicate", "leaderboard", "stats",
            "reminder", "lfg", "suggestion", "success",
        )
        for category in categories:
            with self.subTest(category=category):
                self.assertTrue(PHRASES[category])
                if category.startswith("coin_winner"):
                    phrase = pick(category, seed="2026-10-26",
                                  name="@gamer", day="Monday", roll=90, coin="coin")
                else:
                    phrase = pick(category, seed="2026-10-26")
                self.assertTrue(phrase)
                self.assertNotIn("other head", phrase.lower())

    def test_count_singular_plural(self):
        self.assertEqual(count_label(1), "1 coin")
        self.assertEqual(count_label(2), "2 coins")
