"""Pure sorting helpers for the Forever launch-planning board.

Launch is Wednesday 4 November at 23:00 UK. The hour selections after
"Launch 23:00 UK" refer to Thursday 5 November onward. Treat "Later"
and pre-existing free-form times as unscheduled so they follow known starts.
"""

import re


PLAYING_DAYS = (
    "Thu 5 Nov",
    "Fri 6 Nov",
    "Sat 7 Nov",
    "Sun 8 Nov",
    "Mon 9 Nov",
    "Tue 10 Nov",
    "Wed 11 Nov",
    "Thu 12 Nov",
    "Fri 13 Nov",
    "Sat 14 Nov",
    "Sun 15 Nov",
)


def is_solo_or_sorted(activity):
    """Solo and already-sorted groups should be displayed at the bottom."""
    value = str(activity or "")
    return "Full Group / Sorted" in value or "Solo Questing" in value


def is_open_lfm(activity):
    """Only explicit Open and LFM plans mean actively seeking group members."""
    value = str(activity or "")
    if is_solo_or_sorted(value):
        return False
    plans = {part.strip() for part in value.split(",")}
    return bool(plans & {
        "Open / Flexible",
        "Flexible",  # Legacy saved plans.
        "Group Questing (LFM)",
        "Group Questing + Dungeons (LFM)",
    })


def listing_board_key(row):
    """Open/LFM plans first; all other plans at the bottom, no day groupings."""
    return (int(not is_open_lfm(row["activity"])), listing_start_key(row))


def listing_start_key(row):
    """Sort earliest planned start first; equal starts sort by character.

    No database migration needed: existing availability and playing days
    already contain enough information to derive the order on every refresh.
    """
    availability = str(row["availability"] or "").strip()
    start = availability.split(" - ", 1)[0].split(" + ", 1)[0].strip()
    character = str(row["character"] or "").casefold()
    user_id = int(row["user_id"])

    if start.startswith("Launch 23:00"):
        return (0, 0, 0, character, user_id)

    match = re.fullmatch(r"([01][0-9]|2[0-2]):00(?: UK)?", start)
    if match:
        selected = [
            part.strip() for part in str(row["days_off"] or "").split(",")
        ]
        # The hourly choices begin on Thu 5 Nov. If an older entry doesn't
        # have recognisable days, default to that first post-launch day.
        first_day = next(
            (
                index for index, day in enumerate(PLAYING_DAYS)
                if any(value == day or value == day.replace(" Nov", "")
                       for value in selected)
            ),
            0,
        )
        return (1, first_day, int(match.group(1)), character, user_id)

    if start == "Later":
        return (2, 0, 0, character, user_id)

    # Older free-text listings without a parseable start stay visible at end.
    return (3, 0, 0, character, user_id)
