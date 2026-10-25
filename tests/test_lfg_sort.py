"""Offline checks for earliest-first Forever launch board sorting."""

import unittest

from cogs.lfg_sort import listing_start_key


def entry(name, availability, days="Thu 5 Nov", user_id=1):
    return {
        "character": name,
        "availability": availability,
        "days_off": days,
        "user_id": user_id,
    }


class LFGSortTests(unittest.TestCase):
    def test_launch_then_overnight_then_daytime_then_later(self):
        records = [
            entry("Late", "Later - 21:00 UK", "Sat 7 Nov", 5),
            entry("Morning", "04:00 UK - 22:00 UK", "Thu 5 Nov", 4),
            entry("Launch", "Launch 23:00 UK - 02:00 UK", "Launch Night · Wed 4 Nov", 3),
            entry("Midnight", "00:00 UK - 06:00 UK", "Thu 5 Nov", 2),
            entry("Legacy", "On now", "4 days", 6),
            entry("Night", "02:00 UK + 24 hours", "Thu 5 Nov", 1),
        ]
        self.assertEqual(
            [r["character"] for r in sorted(records, key=listing_start_key)],
            ["Launch", "Midnight", "Night", "Morning", "Late", "Legacy"],
        )

    def test_first_playing_day_matters_when_start_hour_matches(self):
        records = [
            entry("Friday", "04:00 UK - 22:00 UK", "Fri 6 Nov", 10),
            entry("Thursday", "06:00 UK - 10:00 UK", "Thu 5 Nov", 11),
            entry("Saturday", "01:00 UK - 22:00 UK", "Sat 7 Nov", 12),
        ]
        self.assertEqual(
            [r["character"] for r in sorted(records, key=listing_start_key)],
            ["Thursday", "Friday", "Saturday"],
        )

    def test_equal_starts_stable_name_then_discord_user_id(self):
        records = [
            entry("Zed", "05:00 UK - 18:00 UK", user_id=2),
            entry("Ada", "05:00 UK - 18:00 UK", user_id=9),
            entry("Ada", "05:00 UK - 18:00 UK", user_id=3),
        ]
        self.assertEqual(
            [r["user_id"] for r in sorted(records, key=listing_start_key)],
            [3, 9, 2],
        )

    def test_updating_start_time_reorders_the_same_entry(self):
        first = entry("Alpha", "18:00 UK - 23:00 UK", user_id=1)
        second = entry("Beta", "04:00 UK - 10:00 UK", user_id=2)
        self.assertEqual(sorted((first, second), key=listing_start_key)[0], second)
        first["availability"] = "01:00 UK - 10:00 UK"
        self.assertEqual(sorted((first, second), key=listing_start_key)[0], first)


if __name__ == "__main__":
    unittest.main()
