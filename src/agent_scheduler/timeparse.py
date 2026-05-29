from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$")
_DURATION_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def parse_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def parse_duration(value: str) -> timedelta:
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError("duration must look like 10m, 2h, 1d, or 1w")
    amount = int(match.group(1))
    unit = _DURATION_UNITS[match.group(2)]
    return timedelta(**{unit: amount})


def parse_due_time(*, at: str | None, in_: str | None, now: datetime | None = None) -> datetime:
    if bool(at) == bool(in_):
        raise ValueError("provide exactly one of --at or --in")
    base = now or utc_now()
    if at:
        return parse_datetime(at)
    assert in_ is not None
    return (base + parse_duration(in_)).replace(microsecond=0)


def format_dt(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

