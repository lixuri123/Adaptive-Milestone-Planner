"""Timezone-safe datetime helpers for Planner records."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


def parse_datetime(value: str | datetime | None, timezone_name: str) -> datetime | None:
    """Parse an ISO value and return an aware UTC datetime.

    Naive values intentionally use the Planner profile timezone instead of
    the host machine timezone.  This keeps gateway and CLI behavior identical.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            try:
                parsed_date = date.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError(
                    f"invalid datetime {value!r}; use ISO 8601 such as "
                    "2026-09-10T09:00:00+08:00"
                ) from exc
            parsed = datetime.combine(parsed_date, time.min)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=resolve_timezone(timezone_name))
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def utc_iso(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def local_display(value: str | None, timezone_name: str) -> str | None:
    if not value:
        return None
    parsed = parse_datetime(value, timezone_name)
    if parsed is None:
        return None
    return parsed.astimezone(resolve_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M")


def local_iso(value: str | datetime, timezone_name: str) -> str:
    parsed = parse_datetime(value, timezone_name)
    if parsed is None:
        raise ValueError("datetime is required")
    return parsed.astimezone(resolve_timezone(timezone_name)).replace(
        microsecond=0
    ).isoformat()
