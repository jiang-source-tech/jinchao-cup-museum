from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


XIAOXIN_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_datetime(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("xiaoxin local time requires an aware datetime")
    return current.astimezone(XIAOXIN_TIMEZONE)


def local_date_text(value: datetime | None = None) -> str:
    return local_datetime(value).date().isoformat()
