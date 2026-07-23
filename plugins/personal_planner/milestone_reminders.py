"""Pure selection and rendering for contextual milestone reminders."""

from __future__ import annotations

from datetime import datetime, timedelta

from .timeutil import parse_datetime, utc_iso


def render_milestone_reminder(milestone: dict, *, summary: dict) -> str:
    definition = "; ".join(milestone.get("definition_of_done") or [])
    return (
        f"Today's target: {milestone['title']} "
        f"({int(milestone['target_minutes'])} minutes). "
        f"Done means: {definition}. "
        f"Remaining workload: P50 {int(summary['p50_remaining_minutes'])} minutes, "
        f"P90 {int(summary['p90_remaining_minutes'])} minutes. "
        f"Internal deadline in {int(summary['days_to_internal_deadline'])} days; "
        f"hard deadline in {int(summary['days_to_hard_deadline'])} days. "
        f"Risk: {summary['risk']}. "
        "Reply done, partial + minutes, not started, blocked, or snooze."
    )


def due_milestone_reminders(
    *,
    milestones: list[dict],
    fixed_commitments: list[dict],
    sent: list[dict],
    now: datetime,
    merge_minutes: int,
    recovery_minutes: int,
) -> list[dict]:
    blocked_keys = {
        str(row["dedupe_key"])
        for row in sent
        if row.get("status") in {"pending", "sent"}
    }
    candidates = []
    for milestone in sorted(milestones, key=lambda row: row["id"]):
        if milestone["status"] not in {"planned", "in_progress"}:
            continue
        anchor = milestone.get("reminder_anchor") or {}
        raw_anchor = (
            anchor.get("at")
            if anchor.get("kind") == "snoozed_until"
            else anchor.get("recommended_at")
        )
        scheduled = parse_datetime(raw_anchor, "UTC")
        if scheduled is None or scheduled > now:
            continue
        dedupe_key = f"{milestone['id']}:{utc_iso(scheduled)}"
        if dedupe_key in blocked_keys:
            continue
        deliver_at = now
        for commitment in fixed_commitments:
            starts = parse_datetime(commitment.get("starts_at"), "UTC")
            ends = parse_datetime(commitment.get("ends_at"), "UTC")
            if starts and ends and starts <= now < ends:
                deliver_at = max(
                    deliver_at, ends + timedelta(minutes=recovery_minutes)
                )
        candidates.append(
            {
                "deliver_at": deliver_at,
                "dedupe_keys": [dedupe_key],
                "milestones": [milestone],
                "scheduled_for": utc_iso(scheduled),
            }
        )

    groups = []
    merge_delta = timedelta(minutes=max(0, int(merge_minutes)))
    for candidate in sorted(
        candidates, key=lambda row: (row["deliver_at"], row["dedupe_keys"][0])
    ):
        if (
            groups
            and candidate["deliver_at"] - groups[-1]["deliver_at"] <= merge_delta
        ):
            groups[-1]["deliver_at"] = max(
                groups[-1]["deliver_at"], candidate["deliver_at"]
            )
            groups[-1]["dedupe_keys"].extend(candidate["dedupe_keys"])
            groups[-1]["milestones"].extend(candidate["milestones"])
        else:
            groups.append(candidate)
    for group in groups:
        group["deliver_at"] = utc_iso(group["deliver_at"])
        deliver_at = parse_datetime(group["deliver_at"], "UTC")
        group["ready"] = bool(deliver_at and deliver_at <= now)
    return groups


__all__ = ["due_milestone_reminders", "render_milestone_reminder"]
