"""Message ordering for the live LFG board and the read-only preview.

Every player seeking others has a separate Components V2 message. Non-LFM
plans stay in dedicated embeds. The image footer is always the final message.
"""

CLOSED_EMBED_SIZE = 10


def chunks(rows, size):
    return [list(rows[index:index + size]) for index in range(0, len(rows), size)]


def split_audience(rows, is_open):
    open_rows = [row for row in rows if is_open(row["activity"])]
    closed_rows = [row for row in rows if not is_open(row["activity"])]
    return open_rows, closed_rows


def page_specs(open_rows, closed_rows):
    """All messages after the persistent header, in display order."""
    return (
        [("entry", row) for row in open_rows]
        + [("closed", group) for group in chunks(closed_rows, CLOSED_EMBED_SIZE)]
        + [("footer", None)]
    )
