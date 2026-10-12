"""Account for elapsed UTC time in local calendar-day buckets."""
from datetime import datetime, timedelta, timezone


def add_duration(totals: dict, state: str, start: datetime, end: datetime, tz) -> None:
    """Add only the observed half-open interval, respecting local DST midnight."""
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    while start < end:
        local = start.astimezone(tz)
        midnight = datetime.combine(
            local.date() + timedelta(days=1), datetime.min.time(), tz
        ).astimezone(timezone.utc)
        stop = min(end, midnight)
        values = totals.setdefault(local.date().isoformat(), {
            "on_seconds": 0.0, "off_seconds": 0.0, "unknown_seconds": 0.0,
        })
        values[f"{state}_seconds"] += (stop - start).total_seconds()
        start = stop
