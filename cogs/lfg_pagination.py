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


# The public board no longer has user-facing pagination. It shows consecutive
# messages in the channel when one Discord Components V2 message fills up.
# Seven avatar cards + header + five buttons + footer = 40 components.
BOARD_CHUNK_SIZE = 7
BOARD_TEXT_BUDGET = 3400


def board_chunks(roster):
    """Pack every entry into scrollable Discord messages with no page controls.

    This conservative text budget accounts for labels, formatting, icons and
    player descriptions added by the renderer. Oversized dungeon requests are
    placed in their own message; the form has individual field length limits.
    """
    chunks, current, total = [], [], 200
    for entry in roster:
        # Include fixed markup/labels/emoji overhead, with a conservative
        # estimate of field lengths for mixed launch and dungeon records.
        estimate = 200 + sum(len(str(value)) for value in entry.values())
        if current and (len(current) >= BOARD_CHUNK_SIZE or
                        total + estimate > BOARD_TEXT_BUDGET):
            chunks.append(current)
            current, total = [], 100
        current.append(entry)
        total += estimate
    if current or not chunks:
        chunks.append(current)
    return chunks
