"""Pure daily-review analysis for Personal Planner."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from .timeutil import parse_datetime


def build_daily_review(
    *,
    day_blocks: list[dict[str, Any]],
    future_blocks: list[dict[str, Any]],
    progress: list[dict[str, Any]],
    checkins: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    external_events: list[dict[str, Any]],
    now: datetime,
    day_start: datetime,
    day_end: datetime,
    lookahead_end: datetime,
    timezone_name: str,
    overdue_threshold_minutes: int,
) -> dict[str, Any]:
    """Summarize recorded progress without pretending it maps perfectly to blocks."""
    progress_by_task: dict[str, int] = defaultdict(int)
    for entry in progress:
        progress_by_task[entry["task_id"]] += int(entry["minutes"])

    cutoff = day_start if now < day_start else min(now, day_end)
    remaining_credit = dict(progress_by_task)
    overdue_blocks = []
    remaining_blocks = []
    completed_blocks = []
    for block in day_blocks:
        end = parse_datetime(block["ends_at"], timezone_name)
        if end is None:
            continue
        if block["status"] == "done" or block["task_status"] == "done":
            completed_blocks.append(block)
            continue
        if block["status"] != "confirmed":
            continue
        if end <= cutoff:
            credit = min(int(block["minutes"]), remaining_credit.get(block["task_id"], 0))
            remaining_credit[block["task_id"]] = max(
                0, remaining_credit.get(block["task_id"], 0) - credit
            )
            unaccounted = int(block["minutes"]) - credit
            if unaccounted > 0:
                overdue_blocks.append(
                    {**block, "recorded_progress_credit": credit, "unaccounted_minutes": unaccounted}
                )
        else:
            remaining_blocks.append(block)

    conflicts = []
    for block in future_blocks:
        if block["status"] != "confirmed" or block["task_status"] == "done":
            continue
        block_start = parse_datetime(block["starts_at"], timezone_name)
        block_end = parse_datetime(block["ends_at"], timezone_name)
        if block_start is None or block_end is None or block_end <= now:
            continue
        for event in external_events:
            event_start = parse_datetime(event["starts_at"], timezone_name)
            event_end = parse_datetime(event["ends_at"], timezone_name)
            if (
                event_start is not None
                and event_end is not None
                and event_end > block_start
                and event_start < block_end
            ):
                conflicts.append(
                    {
                        "block_id": block["id"],
                        "plan_id": block["plan_id"],
                        "task_id": block["task_id"],
                        "task_title": block["title"],
                        "block_start": block["starts_at"],
                        "block_end": block["ends_at"],
                        "calendar_id": event["calendar_id"],
                        "calendar_event_id": event["event_id"],
                        "calendar_summary": event["summary"],
                        "event_start": event["starts_at"],
                        "event_end": event["ends_at"],
                    }
                )

    reserved_by_task: dict[str, int] = defaultdict(int)
    for block in future_blocks:
        end = parse_datetime(block["ends_at"], timezone_name)
        if (
            block["status"] == "confirmed"
            and block["task_status"] != "done"
            and end is not None
            and end > now
        ):
            reserved_by_task[block["task_id"]] += int(block["minutes"])

    deadline_risks = []
    for task in tasks:
        if task["status"] not in {"backlog", "planned"}:
            continue
        due = parse_datetime(task.get("due_at"), timezone_name)
        if due is None or due > lookahead_end:
            continue
        remaining = int(task["remaining_minutes"])
        reserved = reserved_by_task.get(task["id"], 0)
        if remaining > reserved:
            deadline_risks.append(
                {
                    "task_id": task["id"],
                    "title": task["title"],
                    "due_at": task["due_at"],
                    "remaining_minutes": remaining,
                    "future_scheduled_minutes": reserved,
                    "uncovered_minutes": remaining - reserved,
                }
            )

    overdue_minutes = sum(
        int(block["unaccounted_minutes"]) for block in overdue_blocks
    )
    reasons = []
    if overdue_minutes >= overdue_threshold_minutes and overdue_minutes > 0:
        reasons.append("overdue_planned_work")
    if conflicts:
        reasons.append("calendar_conflicts")
    if deadline_risks:
        reasons.append("deadline_capacity_gap")
    deferred_checkins = [
        value for value in checkins if value["outcome"] == "deferred"
    ]
    skipped_checkins = [
        value for value in checkins if value["outcome"] == "skipped"
    ]
    if deferred_checkins:
        reasons.append("deferred_blocks")
    if skipped_checkins:
        reasons.append("skipped_blocks")

    planned_minutes = sum(
        int(block["minutes"])
        for block in day_blocks
        if block["status"] in {"confirmed", "done", "skipped"}
    )
    progress_minutes = sum(int(entry["minutes"]) for entry in progress)
    return {
        "planned_minutes": planned_minutes,
        "recorded_progress_minutes": progress_minutes,
        "recorded_progress_ratio": (
            round(progress_minutes / planned_minutes, 3) if planned_minutes else None
        ),
        "completed_blocks": completed_blocks,
        "overdue_blocks": overdue_blocks,
        "overdue_unaccounted_minutes": overdue_minutes,
        "remaining_blocks": remaining_blocks,
        "progress_entries": progress,
        "block_checkins": checkins,
        "deferred_blocks": deferred_checkins,
        "skipped_blocks": skipped_checkins,
        "calendar_conflicts": conflicts,
        "deadline_risks": deadline_risks,
        "replan_recommended": bool(reasons),
        "recommendation_reasons": reasons,
    }


def render_daily_review_message(report: dict[str, Any]) -> str:
    """Render a compact Cron/gateway-friendly review without model involvement."""
    lines = [
        f"Personal Planner review · {report['date']}",
        (
            f"Planned {report['planned_minutes']} min · "
            f"recorded progress {report['recorded_progress_minutes']} min"
        ),
    ]
    if report["overdue_unaccounted_minutes"]:
        lines.append(
            f"Missed or unaccounted planned work: "
            f"{report['overdue_unaccounted_minutes']} min"
        )
    if report["calendar_conflicts"]:
        lines.append(
            f"Calendar conflicts: {len(report['calendar_conflicts'])}"
        )
    if report["deadline_risks"]:
        lines.append(
            f"Near-term deadline capacity gaps: {len(report['deadline_risks'])}"
        )
    if report.get("deferred_blocks"):
        lines.append(f"Deferred blocks: {len(report['deferred_blocks'])}")
    if report.get("skipped_blocks"):
        lines.append(f"Skipped blocks: {len(report['skipped_blocks'])}")
    warnings = report.get("external_calendar", {}).get("warnings") or []
    if warnings:
        lines.append(f"Calendar read warnings: {len(warnings)}")
    if report["replan_recommended"]:
        lines.append("Suggested action: review `/plan review replan`.")
    elif not (
        report["overdue_blocks"]
        or report["calendar_conflicts"]
        or report["deadline_risks"]
        or report.get("deferred_blocks")
        or report.get("skipped_blocks")
    ):
        lines.append("No replan trigger detected.")
    return "\n".join(lines)
