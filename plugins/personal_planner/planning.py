"""Pure rolling-plan scheduling helpers.

The scheduler deliberately stays deterministic and local.  It does not call a
model, mutate calendar data, or hide unscheduled work.  The service persists
the returned proposal as a draft that the user must explicitly confirm.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from .timeutil import parse_datetime, resolve_timezone, utc_iso


ENERGY_RANK = {"low": 0, "medium": 1, "high": 2}
PRIORITY_RANK = {"low": 0, "normal": 20, "high": 45, "critical": 90}


def build_rolling_plan(
    *,
    tasks: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    availability: list[dict[str, Any]],
    busy_intervals: Iterable[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
    timezone_name: str,
    buffer_percent: int,
    max_block_minutes: int,
) -> dict[str, Any]:
    """Allocate task work into recurring availability windows."""
    timezone_value = resolve_timezone(timezone_name)
    slots = _availability_slots(
        availability=availability,
        busy_intervals=busy_intervals,
        window_start=window_start,
        window_end=window_end,
        timezone_name=timezone_name,
    )
    goal_map = {goal["id"]: goal for goal in goals}
    task_state = {
        task["id"]: {
            **task,
            "remaining_minutes": max(0, int(task.get("remaining_minutes") or 0)),
            "_scheduled_minutes": 0,
        }
        for task in tasks
    }

    raw_capacity_by_day: dict[str, int] = defaultdict(int)
    for slot in slots:
        day_key = slot["start"].astimezone(timezone_value).date().isoformat()
        raw_capacity_by_day[day_key] += _minutes(slot["start"], slot["end"])
    usable_capacity_by_day = {
        day: max(0, int(minutes * (100 - buffer_percent) / 100 / 5) * 5)
        for day, minutes in raw_capacity_by_day.items()
    }
    used_by_day: dict[str, int] = defaultdict(int)
    task_blocks_by_day: dict[tuple[str, str], int] = defaultdict(int)
    goal_week_minutes: dict[tuple[str, str], int] = defaultdict(int)
    blocks: list[dict[str, Any]] = []

    for slot in slots:
        cursor = slot["start"]
        day_key = cursor.astimezone(timezone_value).date().isoformat()
        while cursor < slot["end"]:
            daily_remaining = usable_capacity_by_day[day_key] - used_by_day[day_key]
            if daily_remaining <= 0:
                break
            available_minutes = min(
                _minutes(cursor, slot["end"]),
                daily_remaining,
            )
            if available_minutes <= 0:
                break

            candidates = _eligible_tasks(
                task_state.values(),
                cursor=cursor,
                slot_energy=slot["energy"],
                available_minutes=available_minutes,
                max_block_minutes=max_block_minutes,
            )
            if not candidates:
                next_start = _next_eligible_start(
                    task_state.values(),
                    cursor=cursor,
                    slot_end=slot["end"],
                    slot_energy=slot["energy"],
                )
                if next_start is None:
                    break
                cursor = next_start
                continue

            ranked = sorted(
                candidates,
                key=lambda task: (
                    _task_score(
                        task,
                        goal=goal_map.get(task.get("goal_id")),
                        at=cursor,
                        goal_week_minutes=goal_week_minutes,
                        timezone_name=timezone_name,
                    )
                    - task_blocks_by_day[(task["id"], day_key)] * 12,
                    -int(task["remaining_minutes"]),
                    task["id"],
                ),
                reverse=True,
            )
            task = ranked[0]
            block_minutes = _block_minutes(
                task,
                available_minutes=available_minutes,
                max_block_minutes=max_block_minutes,
            )
            if block_minutes <= 0:
                break
            end = cursor + timedelta(minutes=block_minutes)
            score = _task_score(
                task,
                goal=goal_map.get(task.get("goal_id")),
                at=cursor,
                goal_week_minutes=goal_week_minutes,
                timezone_name=timezone_name,
            )
            blocks.append(
                {
                    "task_id": task["id"],
                    "goal_id": task.get("goal_id"),
                    "title": task["title"],
                    "starts_at": utc_iso(cursor),
                    "ends_at": utc_iso(end),
                    "minutes": block_minutes,
                    "energy": task["energy"],
                    "availability_label": slot.get("label"),
                    "priority_score": score,
                }
            )
            task["remaining_minutes"] -= block_minutes
            task["_scheduled_minutes"] += block_minutes
            used_by_day[day_key] += block_minutes
            task_blocks_by_day[(task["id"], day_key)] += 1
            if task.get("goal_id"):
                week_key = _week_key(cursor, timezone_name)
                goal_week_minutes[(task["goal_id"], week_key)] += block_minutes
            cursor = end

    unscheduled = []
    deadline_risks = []
    availability_energy = {
        row["energy"] for row in availability if int(row.get("enabled", 1))
    }
    for task in task_state.values():
        remaining = int(task["remaining_minutes"])
        due = parse_datetime(task.get("due_at"), timezone_name)
        if remaining > 0:
            earliest = parse_datetime(task.get("earliest_start"), timezone_name)
            if not slots:
                reason = "no_available_time"
            elif earliest and earliest >= window_end:
                reason = "not_started_in_window"
            elif not any(
                ENERGY_RANK.get(energy, 0) >= ENERGY_RANK.get(task["energy"], 1)
                for energy in availability_energy
            ):
                reason = "no_compatible_energy_window"
            else:
                reason = "insufficient_capacity"
            entry = {
                "task_id": task["id"],
                "title": task["title"],
                "remaining_minutes": remaining,
                "reason": reason,
                "due_at": task.get("due_at"),
            }
            unscheduled.append(entry)
            if due and due <= window_end:
                deadline_risks.append({**entry, "risk": "deadline_not_fully_scheduled"})

        task_blocks = [block for block in blocks if block["task_id"] == task["id"]]
        if due and task_blocks:
            last_end = parse_datetime(task_blocks[-1]["ends_at"], timezone_name)
            if last_end and last_end > due:
                deadline_risks.append(
                    {
                        "task_id": task["id"],
                        "title": task["title"],
                        "due_at": task["due_at"],
                        "last_planned_end": task_blocks[-1]["ends_at"],
                        "risk": "planned_after_deadline",
                    }
                )

    goal_allocations: list[dict[str, Any]] = []
    for goal in goals:
        allocations = {
            week: minutes
            for (goal_id, week), minutes in goal_week_minutes.items()
            if goal_id == goal["id"]
        }
        goal_allocations.append(
            {
                "goal_id": goal["id"],
                "title": goal["title"],
                "weekly_target_minutes": goal["weekly_target_minutes"],
                "scheduled_by_week": allocations,
            }
        )

    raw_capacity = sum(raw_capacity_by_day.values())
    usable_capacity = sum(usable_capacity_by_day.values())
    scheduled_minutes = sum(block["minutes"] for block in blocks)
    return {
        "blocks": blocks,
        "unscheduled": unscheduled,
        "deadline_risks": _dedupe_risks(deadline_risks),
        "goal_allocations": goal_allocations,
        "capacity": {
            "raw_available_minutes": raw_capacity,
            "buffer_minutes": raw_capacity - usable_capacity,
            "usable_minutes": usable_capacity,
            "scheduled_minutes": scheduled_minutes,
            "unused_minutes": max(0, usable_capacity - scheduled_minutes),
        },
    }


def _availability_slots(
    *,
    availability: list[dict[str, Any]],
    busy_intervals: Iterable[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
    timezone_name: str,
) -> list[dict[str, Any]]:
    timezone_value = resolve_timezone(timezone_name)
    local_start = window_start.astimezone(timezone_value)
    local_end = window_end.astimezone(timezone_value)
    busy = sorted(
        (
            max(start.astimezone(timezone.utc), window_start),
            min(end.astimezone(timezone.utc), window_end),
        )
        for start, end in busy_intervals
        if end > window_start and start < window_end
    )
    rows_by_weekday: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in availability:
        if int(row.get("enabled", 1)):
            rows_by_weekday[int(row["weekday"])].append(row)

    slots: list[dict[str, Any]] = []
    current: date = local_start.date()
    while current <= local_end.date():
        for row in rows_by_weekday.get(current.weekday(), []):
            start_local = _minute_datetime(
                current, int(row["start_minute"]), timezone_value
            )
            end_local = _minute_datetime(
                current, int(row["end_minute"]), timezone_value
            )
            start = max(start_local.astimezone(timezone.utc), window_start)
            end = min(end_local.astimezone(timezone.utc), window_end)
            if end <= start:
                continue
            for free_start, free_end in _subtract_busy(start, end, busy):
                if _minutes(free_start, free_end) >= 5:
                    slots.append(
                        {
                            "start": free_start,
                            "end": free_end,
                            "energy": row["energy"],
                            "label": row.get("label"),
                        }
                    )
        current += timedelta(days=1)
    return sorted(slots, key=lambda slot: (slot["start"], slot["end"]))


def _subtract_busy(
    start: datetime,
    end: datetime,
    busy: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    free = [(start, end)]
    for busy_start, busy_end in busy:
        next_free = []
        for free_start, free_end in free:
            if busy_end <= free_start or busy_start >= free_end:
                next_free.append((free_start, free_end))
                continue
            if busy_start > free_start:
                next_free.append((free_start, busy_start))
            if busy_end < free_end:
                next_free.append((busy_end, free_end))
        free = next_free
    return free


def _eligible_tasks(
    tasks: Iterable[dict[str, Any]],
    *,
    cursor: datetime,
    slot_energy: str,
    available_minutes: int,
    max_block_minutes: int,
) -> list[dict[str, Any]]:
    result = []
    for task in tasks:
        if int(task["remaining_minutes"]) <= 0:
            continue
        earliest = parse_datetime(task.get("earliest_start"), task["timezone"])
        if earliest and earliest > cursor:
            continue
        if ENERGY_RANK[slot_energy] < ENERGY_RANK[task["energy"]]:
            continue
        if _block_minutes(
            task,
            available_minutes=available_minutes,
            max_block_minutes=max_block_minutes,
        ) <= 0:
            continue
        result.append(task)
    return result


def _next_eligible_start(
    tasks: Iterable[dict[str, Any]],
    *,
    cursor: datetime,
    slot_end: datetime,
    slot_energy: str,
) -> datetime | None:
    values = []
    for task in tasks:
        if int(task["remaining_minutes"]) <= 0:
            continue
        if ENERGY_RANK[slot_energy] < ENERGY_RANK[task["energy"]]:
            continue
        earliest = parse_datetime(task.get("earliest_start"), task["timezone"])
        if earliest and cursor < earliest < slot_end:
            values.append(earliest)
    return min(values) if values else None


def _block_minutes(
    task: dict[str, Any],
    *,
    available_minutes: int,
    max_block_minutes: int,
) -> int:
    remaining = int(task["remaining_minutes"])
    if not int(task["splittable"]):
        return remaining if remaining <= available_minutes else 0
    candidate = min(remaining, available_minutes, max_block_minutes)
    minimum = int(task["min_block_minutes"])
    if candidate < minimum and candidate != remaining:
        return 0
    return candidate


def _task_score(
    task: dict[str, Any],
    *,
    goal: dict[str, Any] | None,
    at: datetime,
    goal_week_minutes: dict[tuple[str, str], int],
    timezone_name: str,
) -> int:
    score = PRIORITY_RANK[task["priority"]] + ENERGY_RANK[task["energy"]] * 10
    due = parse_datetime(task.get("due_at"), timezone_name)
    if due:
        hours = (due - at).total_seconds() / 3600
        if hours < 0:
            score += 220
        elif hours <= 24:
            score += 140
        elif hours <= 72:
            score += 100
        elif hours <= 7 * 24:
            score += 65
        elif hours <= 14 * 24:
            score += 35
    if goal:
        score += PRIORITY_RANK[goal["priority"]] // 2
        target = int(goal["weekly_target_minutes"])
        allocated = goal_week_minutes[(goal["id"], _week_key(at, timezone_name))]
        if target > 0:
            score += 35 if allocated < target else -20
    return score


def _week_key(value: datetime, timezone_name: str) -> str:
    local = value.astimezone(resolve_timezone(timezone_name))
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def _minutes(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def _minute_datetime(
    day: date, value: int, timezone_value: Any
) -> datetime:
    if value == 24 * 60:
        return datetime.combine(day + timedelta(days=1), time.min, timezone_value)
    return datetime.combine(
        day,
        time(hour=value // 60, minute=value % 60),
        timezone_value,
    )


def _dedupe_risks(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for value in values:
        key = (value["task_id"], value["risk"])
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
