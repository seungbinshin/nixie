"""Free-slot calculation on the owner's calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


def _overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def free_slots(
    window: Interval,
    duration: timedelta,
    busy: list[Interval],
    tz: ZoneInfo,
    work_hours: tuple[int, int] = (9, 18),
    step: timedelta = timedelta(minutes=30),
    limit: int = 20,
) -> list[Interval]:
    """Return candidate slots inside the window, inside work hours, not overlapping busy intervals."""
    start, end = window[0].astimezone(tz), window[1].astimezone(tz)
    # align to step
    minute = (start.minute // 30) * 30
    t = start.replace(minute=minute, second=0, microsecond=0)
    if t < start:
        t += step
    out: list[Interval] = []
    while t + duration <= end and len(out) < limit:
        slot = (t, t + duration)
        day_start = t.replace(hour=work_hours[0], minute=0)
        day_end = t.replace(hour=work_hours[1], minute=0)
        if slot[0] >= day_start and slot[1] <= day_end and not any(_overlaps(slot, b) for b in busy):
            out.append(slot)
        t += step
    return out


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Slots present in both lists. Compares instants, so KST and UTC forms of one slot match."""
    bset = {(s.timestamp(), e.timestamp()) for s, e in b}
    return [s for s in a if (s[0].timestamp(), s[1].timestamp()) in bset]
