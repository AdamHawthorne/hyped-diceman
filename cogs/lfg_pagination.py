"""Pagination and audience grouping for the persistent LFG noticeboard.

Discord V2 permits 40 components and 4000 text characters per message.
Five avatar rows per page gives room for dividers, heading and controls.
"""

OPEN_PAGE_SIZE = 5
CLOSED_EMBED_SIZE = 10


def chunks(rows, size):
    return [list(rows[index:index + size]) for index in range(0, len(rows), size)]


def split_audience(rows, is_open):
    open_rows = [row for row in rows if is_open(row["activity"])]
    closed_rows = [row for row in rows if not is_open(row["activity"])]
    return open_rows, closed_rows


def page_specs(open_rows, closed_rows):
    """Additional messages in strict message order, after the live anchor."""
    extra_open = chunks(open_rows, OPEN_PAGE_SIZE)[1:]
    return (
        [("v2", part) for part in extra_open]
        + [("closed", part) for part in chunks(closed_rows, CLOSED_EMBED_SIZE)]
    )
