from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid ISO timestamp") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def plus_seconds(value: str, seconds: int) -> str:
    return (parse_iso(value) + timedelta(seconds=seconds)).isoformat()


def minus_seconds(value: str, seconds: int) -> str:
    return (parse_iso(value) - timedelta(seconds=seconds)).isoformat()


def stale_before_iso(*, now: datetime | None = None, seconds: int) -> str:
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    return (current - timedelta(seconds=seconds)).isoformat()


def submit_stale_at(submit_started_at: str, *, stale_after_seconds: int) -> datetime:
    return parse_iso(submit_started_at) + timedelta(seconds=stale_after_seconds)
