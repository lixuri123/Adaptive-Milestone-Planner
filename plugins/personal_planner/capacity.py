"""Pure daily capacity aggregation around fixed commitments."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .timeutil import parse_datetime, resolve_timezone, utc_iso


def build_daily_capacity(
    *,
    availability: list[dict],
    fixed_commitments: list[dict],
    window_start: datetime,
    window_end: datetime,
    timezone_name: str,
    buffer_percent: int,
    recovery_minutes: int,
) -> list[dict]:
    """Aggregate recurring availability into deterministic local-day budgets."""

    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("capacity window bounds must include a timezone")
    if window_end <= window_start:
        raise ValueError("capacity window_end must be later than window_start")
    buffer_percent = max(0, min(int(buffer_percent), 80))
    recovery_minutes = max(0, min(int(recovery_minutes), 240))
    timezone_value = resolve_timezone(timezone_name)
    start_utc = window_start.astimezone(timezone.utc)
    end_utc = window_end.astimezone(timezone.utc)
    commitments = _normalize_commitments(
        fixed_commitments,
        timezone_name=timezone_name,
        recovery_minutes=recovery_minutes,
    )

    current = start_utc.astimezone(timezone_value).date()
    last = (end_utc - timedelta(microseconds=1)).astimezone(timezone_value).date()
    result: list[dict] = []
    while current <= last:
        windows = _local_windows(
            current,
            availability,
            timezone_value,
            window_start=start_utc,
            window_end=end_utc,
        )
        relevant = [
            item
            for item in commitments
            if any(item[1] > start and item[0] < end for start, end in windows)
        ]
        free_intervals = _subtract_intervals(
            windows, [(item[0], item[1]) for item in relevant]
        )
        raw_minutes = sum(_minutes(start, end) for start, end in free_intervals)
        usable = max(
            0,
            int(raw_minutes * (100 - buffer_percent) / 100 / 5) * 5,
        )
        first_free = min(free_intervals, key=lambda item: item[0], default=None)
        preceding = max(
            (
                item
                for item in relevant
                if first_free is not None and item[1] <= first_free[0]
            ),
            key=lambda item: (item[1], item[2]),
            default=None,
        )
        if preceding is not None:
            anchor = {
                "kind": "after_commitment",
                "commitment_id": preceding[2],
                "offset_minutes": recovery_minutes,
                "recommended_at": utc_iso(first_free[0]),
            }
        else:
            anchor = {
                "kind": "first_suitable_window",
                "recommended_at": utc_iso(first_free[0]) if first_free else None,
            }
        result.append(
            {
                "date": current.isoformat(),
                "raw_minutes": raw_minutes,
                "buffer_minutes": raw_minutes - usable,
                "capacity_minutes": usable,
                "capacity_class": (
                    "low" if usable < 90 else "medium" if usable < 240 else "high"
                ),
                "confidence": 0.85 if windows else 0.35,
                "reasons": list(
                    dict.fromkeys(item[3] for item in relevant if item[3])
                ),
                "reminder_anchor": anchor,
            }
        )
        current += timedelta(days=1)
    return result


def _local_windows(
    day: date,
    availability: list[dict],
    timezone_value: Any,
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    values: list[tuple[datetime, datetime]] = []
    for row in availability:
        if not int(row.get("enabled", 1)) or int(row["weekday"]) != day.weekday():
            continue
        start = _minute_datetime(day, int(row["start_minute"]), timezone_value)
        end = _minute_datetime(day, int(row["end_minute"]), timezone_value)
        start = max(start.astimezone(timezone.utc), window_start)
        end = min(end.astimezone(timezone.utc), window_end)
        if end > start:
            values.append((start, end))
    return _merge_intervals(values)


def _normalize_commitments(
    values: list[dict], *, timezone_name: str, recovery_minutes: int
) -> list[tuple[datetime, datetime, str, str]]:
    result: list[tuple[datetime, datetime, str, str]] = []
    for index, value in enumerate(values):
        start = parse_datetime(value.get("starts_at"), timezone_name)
        end = parse_datetime(value.get("ends_at"), timezone_name)
        if start is None or end is None or end <= start:
            continue
        result.append(
            (
                start,
                end + timedelta(minutes=recovery_minutes),
                str(value.get("id") or f"commitment-{index + 1}"),
                str(value.get("title") or value.get("summary") or "Busy"),
            )
        )
    return sorted(result, key=lambda item: (item[0], item[1], item[2]))


def _subtract_intervals(
    windows: list[tuple[datetime, datetime]],
    busy: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    free = list(windows)
    for busy_start, busy_end in _merge_intervals(busy):
        next_free: list[tuple[datetime, datetime]] = []
        for free_start, free_end in free:
            if busy_end <= free_start or busy_start >= free_end:
                next_free.append((free_start, free_end))
                continue
            if busy_start > free_start:
                next_free.append((free_start, busy_start))
            if busy_end < free_end:
                next_free.append((busy_end, free_end))
        free = next_free
    return sorted(free)


def _merge_intervals(
    values: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    merged: list[list[datetime]] = []
    for start, end in sorted(values):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _minute_datetime(day: date, value: int, timezone_value: Any) -> datetime:
    if value == 24 * 60:
        return datetime.combine(day + timedelta(days=1), time.min, timezone_value)
    return datetime.combine(
        day,
        time(hour=value // 60, minute=value % 60),
        timezone_value,
    )


def _minutes(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


__all__ = ["build_daily_capacity"]
