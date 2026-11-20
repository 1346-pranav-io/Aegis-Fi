"""UTC-only time helpers (D-17)."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def require_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(UTC)
