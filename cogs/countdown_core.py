"""Pure clock, DST and reminder calculations for Hyped Ogre countdowns.

All timestamps are stored as UTC epoch seconds. Input is local Europe/London
time, so UK daylight-saving changes do not shift stored event times.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")


def parse_uk_datetime(value):
    """Parse YYYY-MM-DD HH:MM in UK time, rejecting DST gaps/ambiguity."""
    try:
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except (ValueError, AttributeError) as exc:
        raise ValueError("Use YYYY-MM-DD HH:MM (UK time), e.g. 2026-11-04 23:00.") from exc

    first = naive.replace(tzinfo=UK, fold=0)
    second = naive.replace(tzinfo=UK, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError(
            "That UK time is ambiguous or falls in the clock change. "
            "Choose an unambiguous hour."
        )
    result = int(first.timestamp())
    if datetime.fromtimestamp(result, UK).replace(tzinfo=None) != naive:
        raise ValueError("That local time does not exist in the UK.")
    return result


def upcoming_string(target_ts, now_ts):
    """Countdown in days, hours and minutes (round partial minutes up)."""
    remain = max(0, int(target_ts) - int(now_ts))
    if remain == 0:
        return "00m · Ready"
    total_minutes = (remain + 59) // 60
    days, rest = divmod(total_minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def remaining_hours_string(target_ts, now_ts):
    """Compact total-hours countdown, e.g. 241h 07m remaining."""
    remain = max(0, int(target_ts) - int(now_ts))
    if remain == 0:
        return "Ready!"
    total_minutes = (remain + 59) // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m remaining"
    return f"{minutes}m remaining"


def next_cycle(target_ts, repeat_seconds, now_ts):
    """First repeated target strictly after now, skipping any missed cycles."""
    if repeat_seconds <= 0 or target_ts > now_ts:
        return int(target_ts)
    cycles = (int(now_ts) - int(target_ts)) // int(repeat_seconds) + 1
    return int(target_ts) + cycles * int(repeat_seconds)


def pre_reminder_due(target_ts, now_ts, lead_minutes, last_target):
    return (
        lead_minutes > 0
        and last_target != target_ts
        and 0 < target_ts - now_ts <= lead_minutes * 60
    )


def due_reminder_due(target_ts, now_ts, last_target):
    return now_ts >= target_ts and last_target != target_ts


def render_key(target_ts, now_ts, active):
    """Keep one-minute updates cheap; a completed timer stops changing."""
    if not active or now_ts >= target_ts:
        return f"{target_ts}:{int(bool(active))}:done"
    remaining_minutes = (target_ts - now_ts + 59) // 60
    return f"{target_ts}:{int(bool(active))}:{remaining_minutes}"


def validate_image_url(value):
    """Accept public HTTPS images, including PNGs uploaded to i.vgy.me."""
    if value is None or not str(value).strip():
        return ""
    url = str(value).strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username:
        raise ValueError("Image must be a public HTTPS URL from vgy.me or another image host.")
    if len(url) > 1024 or any(c in url for c in (" ", "\n", "\r", "<", ">")):
        raise ValueError("Image URL is too long or contains invalid characters.")
    return url


def uk_display(target_ts):
    return datetime.fromtimestamp(int(target_ts), UK).strftime("%a %-d %b %Y · %-I:%M%p UK")
