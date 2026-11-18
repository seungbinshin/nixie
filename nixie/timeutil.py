"""Time rules.

- Storage and wire format: UTC, ISO 8601, with a "Z" suffix (2026-11-17T04:00:00Z).
- Input without an offset is local time of the owner (Asia/Seoul by default).
- Text for people shows KST (Asia/Seoul).
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def utc_iso(dt: datetime, timespec: str = "seconds") -> str:
    """UTC ISO 8601 with Z. A naive datetime is not allowed (its zone is unknown)."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime: add a timezone first")
    return dt.astimezone(timezone.utc).isoformat(timespec=timespec).replace("+00:00", "Z")


def now_iso() -> str:
    return utc_iso(datetime.now(timezone.utc), "milliseconds")


def parse(s: str, tz: ZoneInfo = KST) -> datetime:
    """Parse ISO 8601. Without an offset the value is local time in `tz`."""
    d = datetime.fromisoformat(str(s))
    return d if d.tzinfo else d.replace(tzinfo=tz)


def to_utc(s: str, tz: ZoneInfo = KST) -> str:
    return utc_iso(parse(s, tz))


def kst(s: str | datetime, fmt: str = "%m/%d %H:%M") -> str:
    d = parse(s) if isinstance(s, str) else s
    return d.astimezone(KST).strftime(fmt)
