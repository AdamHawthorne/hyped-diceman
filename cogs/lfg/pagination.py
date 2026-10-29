"""Pure pagination helpers for a single edited Components V2 LFG message.

A separate V2 container represents each player. Four entries per page leave
room for the header, editing and navigation controls, and the final image.
No member listing is omitted: viewers use Back/Next on the same message.
"""

PAGE_SIZE = 4


def page_count(total):
    """One page also exists when the roster is empty."""
    return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)


def clamp_page(index, total):
    """If a player leaves and a page disappears, show the new last page."""
    return max(0, min(int(index), page_count(total) - 1))


def page_rows(roster, index):
    """Return one contiguous slice, without regrouping or changing order."""
    index = clamp_page(index, len(roster))
    start = index * PAGE_SIZE
    return roster[start:start + PAGE_SIZE]
