"""Testable clock. Governance validity is always derived from this clock on read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp {value!r} must include a UTC offset")
    return dt.astimezone(UTC)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock(Clock):
    def __init__(self, at: datetime):
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at.astimezone(UTC)

    def advance(self, **kwargs: float) -> None:
        self._at = self._at + timedelta(**kwargs)


def build_clock(fixed: str) -> Clock:
    return FixedClock(parse_utc(fixed)) if fixed else Clock()
