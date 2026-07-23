"""Conversation ``/plan`` command."""

from __future__ import annotations

import json
import shlex
from typing import Any

from .cron_support import (
    automation_status,
    ensure_automation_jobs,
    ensure_radar_job,
    pause_automation_jobs,
)
from .expert_agents import route_domain_agents
from .service import PlannerError, PlannerService
from .timeutil import local_display


HELP = """Personal Planner commands:
/plan inbox
/plan today
/plan overview
/plan capture <notification text>
/plan revisions <P-ID>
/plan confirm <P-ID>
/plan ack <P-ID>
/plan snooze <P-ID> <ISO timestamp>
/plan done <P-ID>
/plan cancel <P-ID>
/plan sources
/plan source-add <name> <gmail-query>
/plan source-create <JSON fields>
/plan source-show <F-ID>
/plan source-enable <F-ID>
/plan source-disable <F-ID>
/plan source-scan [F-ID]
/plan profile
/plan profile-add <category> <key> <value>
/plan profile-show <PF-ID>
/plan profile-confirm <PF-ID>
/plan profile-reject <PF-ID>
/plan profile-revoke <PF-ID>
/plan radar-propose
/plan monitors
/plan monitor-show <MP-ID>
/plan monitor-approve <MP-ID>
/plan monitor-reject <MP-ID>
/plan calendar-preview <P-ID>
/plan calendar-sync <P-ID> confirm
/plan calendar-forget <P-ID> confirm
/plan goals
/plan goal-add <title>
/plan goal-create <JSON fields>
/plan goal-status <G-ID> <active|paused|done|cancelled>
/plan tasks
/plan task-add <title>
/plan task-create <JSON fields>
/plan task-update <T-ID> <JSON fields>
/plan task-progress <T-ID> <minutes>
/plan task-done <T-ID>
/plan task-cancel <T-ID>
/plan availability
/plan availability-add <weekday> <HH:MM> <HH:MM> [energy] [label]
/plan availability-clear <A-ID>
/plan calendar-busy
/plan review [replan]
/plan schedule [calendar]
/plan schedule-show <S-ID>
/plan schedule-confirm <S-ID> confirm [replace]
/plan schedule-cancel <S-ID>
/plan block-show <B-ID>
/plan block-done <B-ID> [actual-minutes]
/plan block-skip <B-ID> [replan]
/plan block-defer <B-ID> [replan]
/plan analyze <research,career,communication|auto> <text>
/plan milestone-preview [days]
/plan milestone-overview
/plan milestone-show <MP-ID>
/plan milestone-confirm <MP-ID> confirm [replace=<MP-ID>]
/plan milestone-reject <MP-ID>
/plan milestone-checkin <DM-ID> <done|partial|not_started|blocked|skipped> [minutes] [fraction] [note]
/plan milestone-snooze <DM-ID> [minutes]
/plan schedule-calendar-preview <S-ID>
/plan schedule-calendar-sync <S-ID> confirm
/plan schedule-calendar-forget <S-ID> confirm
/plan status
/plan automation-status
/plan automation-enable [delivery]
/plan automation-pause

Use `hermes plan capture ... --actionable-at ... --due-at ...` or the
personal_planner tool when the notification contains structured dates.
Use `hermes plan review-setup` to install the optional daily review Cron job.
"""


def _render_items(items: list[dict]) -> str:
    if not items:
        return "No Planner items found."
    lines: list[str] = []
    for item in items:
        timing = item.get("due_at") or item.get("event_at") or item.get("actionable_at")
        when = local_display(timing, item["timezone"]) if timing else "time not set"
        lines.append(f"- [{item['id']}] {item['title']} · {item['status']} · {when}")
    return "\n".join(lines)


def handle_plan_command(
    raw_args: str,
    *,
    expert_runner: Any | None = None,
    service: PlannerService | None = None,
) -> str:
    try:
        parts = shlex.split(raw_args or "")
    except ValueError as exc:
        return f"Invalid /plan arguments: {exc}"
    if not parts or parts[0] in {"help", "-h", "--help"}:
        return HELP

    service = service or PlannerService()
    action = parts[0].lower()
    try:
        if action in {"inbox", "list", "ls"}:
            return _render_items(service.list())
        if action == "today":
            return _render_items(service.today())
        if action == "overview" and len(parts) == 1:
            overview = service.overview()
            try:
                automation = automation_status()
            except Exception as exc:
                automation = {
                    "enabled": False,
                    "enabled_count": 0,
                    "jobs": {},
                    "error": str(exc),
                }
            radar_enabled = bool(
                (automation.get("jobs") or {}).get("radar", {}).get("enabled")
            )
            overview["automation"] = automation
            for proposal in overview["monitor_proposals"]:
                proposal["network_scan_active"] = (
                    radar_enabled and proposal["status"] == "approved"
                )
            return json.dumps(
                overview,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        if action == "analyze" and len(parts) >= 3:
            if expert_runner is None:
                raise PlannerError(
                    "domain agent analysis is unavailable in this surface"
                )
            domain_arg = parts[1].lower()
            text = " ".join(parts[2:])
            domains = (
                route_domain_agents(text, source_kind="conversation")
                if domain_arg == "auto"
                else list(dict.fromkeys(domain_arg.split(",")))
            )
            event = service.record_event(
                event_type="work.requested",
                source="conversation",
                payload={"text": text},
            )
            return json.dumps(
                service.analyze_event(
                    event["id"], domains=domains, expert_runner=expert_runner
                ),
                ensure_ascii=False,
                indent=2,
            )
        if action == "milestone-preview" and len(parts) in {1, 2}:
            days = int(parts[1]) if len(parts) == 2 else None
            result = service.milestone_preview(days=days)
            return (
                f"Created draft milestone plan [{result['id']}] with "
                f"{len(result['milestones'])} targets. Review it with "
                f"/plan milestone-show {result['id']}."
            )
        if action == "milestone-overview" and len(parts) == 1:
            return json.dumps(
                service.milestone_overview(), ensure_ascii=False, indent=2
            )
        if action == "milestone-show" and len(parts) == 2:
            result = service.store.get_milestone_plan(parts[1])
            if result is None:
                raise PlannerError(f"milestone plan not found: {parts[1]}")
            return json.dumps(result, ensure_ascii=False, indent=2)
        if (
            action == "milestone-confirm"
            and len(parts) in {3, 4}
            and parts[2].lower() == "confirm"
        ):
            replace_plan_id = None
            if len(parts) == 4:
                if not parts[3].lower().startswith("replace="):
                    return HELP
                replace_plan_id = parts[3].split("=", 1)[1].strip() or None
            result = service.confirm_milestone_plan(
                parts[1], confirmed=True, replace_plan_id=replace_plan_id
            )
            return f"Confirmed milestone plan [{result['id']}]."
        if action == "milestone-reject" and len(parts) == 2:
            result = service.reject_milestone_plan(parts[1])
            return f"Rejected milestone plan [{result['id']}]."
        if action == "milestone-checkin" and len(parts) >= 3:
            outcome = parts[2].lower()
            actual_minutes = None
            completion_fraction = None
            note = None
            if outcome == "partial":
                if len(parts) < 5:
                    return (
                        "Usage: /plan milestone-checkin <DM-ID> partial "
                        "<minutes> <fraction> [note]"
                    )
                actual_minutes = int(parts[3])
                completion_fraction = float(parts[4])
                note = " ".join(parts[5:]) or None
            elif outcome == "blocked":
                if len(parts) < 4:
                    return (
                        "Usage: /plan milestone-checkin <DM-ID> blocked <note>"
                    )
                note = " ".join(parts[3:])
            elif outcome in {"done", "not_started", "skipped"}:
                if len(parts) != 3:
                    return (
                        "Usage: /plan milestone-checkin <DM-ID> "
                        "<done|not_started|skipped>"
                    )
            else:
                return HELP
            result = service.milestone_checkin(
                parts[1],
                outcome=outcome,
                actual_minutes=actual_minutes,
                completion_fraction=completion_fraction,
                note=note,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action == "milestone-snooze" and len(parts) in {2, 3}:
            result = service.milestone_snooze(
                parts[1], minutes=int(parts[2]) if len(parts) == 3 else 30
            )
            return (
                f"Snoozed milestone [{result['id']}] until "
                f"{result['reminder_anchor']['at']}."
            )
        if action == "capture":
            if len(parts) < 2:
                return "Usage: /plan capture <notification text>"
            item = service.capture(raw_content=" ".join(parts[1:]))
            outcome = item.get("capture_outcome", "created")
            if outcome == "duplicate":
                return f"Already captured as [{item['id']}] {item['title']}."
            return (
                f"Captured {outcome} draft [{item['id']}] {item['title']}. "
                "Add its dates with `hermes plan` or ask me to extract them before confirming."
            )
        if action == "revisions" and len(parts) == 2:
            revisions = service.revisions(parts[1])
            return json.dumps(revisions, ensure_ascii=False, indent=2)
        if action == "confirm" and len(parts) == 2:
            item = service.confirm(parts[1])
            return f"Confirmed [{item['id']}] {item['title']}."
        if action == "ack" and len(parts) == 2:
            item = service.acknowledge(parts[1])
            return f"Acknowledged [{item['id']}] {item['title']}. Future milestone reminders remain active."
        if action == "snooze" and len(parts) >= 3:
            item = service.snooze(parts[1], " ".join(parts[2:]))
            return (
                f"Snoozed [{item['id']}] {item['title']} until "
                f"{local_display(item['snoozed_until'], item['timezone'])}."
            )
        if action == "done" and len(parts) == 2:
            item = service.complete(parts[1])
            return f"Completed [{item['id']}] {item['title']}. Pending reminders were cancelled."
        if action == "cancel" and len(parts) == 2:
            item = service.cancel(parts[1])
            return f"Cancelled [{item['id']}] {item['title']}. Pending reminders were cancelled."
        if action == "sources" and len(parts) == 1:
            return json.dumps(
                service.list_ingest_sources(),
                ensure_ascii=False,
                indent=2,
            )
        if action == "source-add" and len(parts) >= 3:
            source = service.add_gmail_source(
                name=parts[1],
                query=" ".join(parts[2:]),
            )
            return (
                f"Created Gmail source [{source['id']}] {source['name']}. "
                "Run `/plan source-scan "
                f"{source['id']}` to create review-only drafts."
            )
        if action == "source-create" and len(parts) >= 2:
            values = json.loads(" ".join(parts[1:]))
            if not isinstance(values, dict):
                raise PlannerError("source-create fields must be a JSON object")
            source = service.add_gmail_source(**values)
            return json.dumps(source, ensure_ascii=False, indent=2)
        if action == "source-show" and len(parts) == 2:
            return json.dumps(
                service.get_ingest_source(parts[1]),
                ensure_ascii=False,
                indent=2,
            )
        if action in {"source-enable", "source-disable"} and len(parts) == 2:
            source = service.set_ingest_source_enabled(
                parts[1],
                enabled=action == "source-enable",
            )
            state = "enabled" if source["enabled"] else "disabled"
            return f"Ingestion source [{source['id']}] is {state}."
        if action == "source-scan" and len(parts) in {1, 2}:
            return json.dumps(
                service.scan_ingest_sources(
                    source_id=parts[1] if len(parts) == 2 else None
                ),
                ensure_ascii=False,
                indent=2,
            )
        if action == "profile" and len(parts) == 1:
            return json.dumps(
                service.list_profile_facts(),
                ensure_ascii=False,
                indent=2,
            )
        if action == "profile-add" and len(parts) >= 4:
            fact = service.add_profile_fact(
                category=parts[1],
                fact_key=parts[2],
                fact_value=" ".join(parts[3:]),
                source_type="conversation",
            )
            return (
                f"Created proposed profile fact [{fact['id']}]. "
                f"Review it with `/plan profile-show {fact['id']}`, then "
                f"confirm with `/plan profile-confirm {fact['id']}`."
            )
        if action == "profile-show" and len(parts) == 2:
            return json.dumps(
                service.get_profile_fact(parts[1]),
                ensure_ascii=False,
                indent=2,
            )
        if action in {
            "profile-confirm",
            "profile-reject",
            "profile-revoke",
        } and len(parts) == 2:
            method = {
                "profile-confirm": service.confirm_profile_fact,
                "profile-reject": service.reject_profile_fact,
                "profile-revoke": service.revoke_profile_fact,
            }[action]
            return json.dumps(
                method(parts[1]),
                ensure_ascii=False,
                indent=2,
            )
        if action == "radar-propose" and len(parts) == 1:
            return json.dumps(
                service.generate_monitor_proposals(),
                ensure_ascii=False,
                indent=2,
            )
        if action == "monitors" and len(parts) == 1:
            return json.dumps(
                service.list_monitor_proposals(),
                ensure_ascii=False,
                indent=2,
            )
        if action == "monitor-show" and len(parts) == 2:
            return json.dumps(
                service.get_monitor_proposal(parts[1]),
                ensure_ascii=False,
                indent=2,
            )
        if action in {"monitor-approve", "monitor-reject"} and len(parts) == 2:
            proposal = service.decide_monitor_proposal(
                parts[1],
                decision=(
                    "approved"
                    if action == "monitor-approve"
                    else "rejected"
                ),
            )
            if action == "monitor-approve":
                try:
                    ensured = ensure_radar_job()
                    proposal["network_scan_active"] = bool(
                        ensured["job"].get("enabled", True)
                    )
                    proposal["automation_job_id"] = ensured["job"].get("id")
                except Exception as exc:
                    proposal["network_scan_active"] = False
                    proposal["automation_error"] = str(exc)
            return json.dumps(proposal, ensure_ascii=False, indent=2)
        if action == "calendar-preview" and len(parts) == 2:
            return json.dumps(
                service.calendar_preview(parts[1]), ensure_ascii=False, indent=2
            )
        if action == "calendar-sync" and len(parts) == 3 and parts[2].lower() == "confirm":
            result = service.calendar_sync(parts[1], confirmed=True)
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action == "calendar-forget" and len(parts) == 3 and parts[2].lower() == "confirm":
            result = service.calendar_forget(
                parts[1], confirmed_external_cleanup=True
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action == "goals":
            return json.dumps(
                service.list_goals(status="active"), ensure_ascii=False, indent=2
            )
        if action == "goal-add" and len(parts) >= 2:
            result = service.create_goal(title=" ".join(parts[1:]))
            return f"Created goal [{result['id']}] {result['title']}."
        if action == "goal-create" and len(parts) >= 2:
            values = json.loads(" ".join(parts[1:]))
            if not isinstance(values, dict):
                raise PlannerError("goal-create fields must be a JSON object")
            result = service.create_goal(**values)
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action == "goal-status" and len(parts) == 3:
            result = service.set_goal_status(parts[1], parts[2])
            return f"Goal [{result['id']}] is now {result['status']}."
        if action == "tasks":
            return json.dumps(
                service.list_tasks(), ensure_ascii=False, indent=2
            )
        if action == "task-add" and len(parts) >= 2:
            result = service.create_task(title=" ".join(parts[1:]))
            return f"Created task [{result['id']}] {result['title']}."
        if action == "task-create" and len(parts) >= 2:
            values = json.loads(" ".join(parts[1:]))
            if not isinstance(values, dict):
                raise PlannerError("task-create fields must be a JSON object")
            result = service.create_task(**values)
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action == "task-update" and len(parts) >= 3:
            changes = json.loads(" ".join(parts[2:]))
            if not isinstance(changes, dict):
                raise PlannerError("task-update fields must be a JSON object")
            result = service.update_task(parts[1], changes)
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action == "task-progress" and len(parts) == 3:
            result = service.add_task_progress(parts[1], int(parts[2]))
            return (
                f"Recorded progress for [{result['id']}] {result['title']}; "
                f"{result['remaining_minutes']} minutes remain."
            )
        if action == "task-done" and len(parts) == 2:
            result = service.complete_task(parts[1])
            return f"Completed task [{result['id']}] {result['title']}."
        if action == "task-cancel" and len(parts) == 2:
            result = service.cancel_task(parts[1])
            return f"Cancelled task [{result['id']}] {result['title']}."
        if action == "availability":
            return json.dumps(
                service.list_availability(), ensure_ascii=False, indent=2
            )
        if action == "availability-add" and len(parts) >= 4:
            energy = parts[4] if len(parts) >= 5 else "medium"
            label = " ".join(parts[5:]) if len(parts) >= 6 else None
            return json.dumps(
                service.add_availability(
                    weekday=parts[1],
                    start_time=parts[2],
                    end_time=parts[3],
                    energy=energy,
                    label=label,
                ),
                ensure_ascii=False,
                indent=2,
            )
        if action == "availability-clear" and len(parts) == 2:
            return json.dumps(
                service.clear_availability(availability_id=parts[1]),
                ensure_ascii=False,
                indent=2,
            )
        if action == "calendar-busy" and len(parts) == 1:
            return json.dumps(
                service.calendar_busy(), ensure_ascii=False, indent=2
            )
        if action == "review" and len(parts) in {1, 2}:
            if len(parts) == 2 and parts[1].lower() != "replan":
                return HELP
            return json.dumps(
                service.daily_review(propose_replan=len(parts) == 2),
                ensure_ascii=False,
                indent=2,
            )
        if action == "schedule" and len(parts) in {1, 2}:
            if len(parts) == 2 and parts[1].lower() != "calendar":
                return HELP
            result = service.schedule_preview(use_calendar=len(parts) == 2)
            return (
                f"Created draft schedule [{result['id']}] with "
                f"{len(result['blocks'])} blocks. Review it with "
                f"/plan schedule-show {result['id']}."
            )
        if action == "schedule-show" and len(parts) == 2:
            return json.dumps(
                service.get_schedule(parts[1]), ensure_ascii=False, indent=2
            )
        if (
            action == "schedule-confirm"
            and len(parts) in {3, 4}
            and parts[2].lower() == "confirm"
        ):
            result = service.confirm_schedule(
                parts[1],
                confirmed=True,
                replace=len(parts) == 4 and parts[3].lower() == "replace",
            )
            return f"Confirmed schedule [{result['id']}]."
        if action == "schedule-cancel" and len(parts) == 2:
            result = service.cancel_schedule(parts[1])
            return f"Cancelled schedule [{result['id']}]."
        if action == "block-show" and len(parts) == 2:
            return json.dumps(
                service.get_block(parts[1]), ensure_ascii=False, indent=2
            )
        if action == "block-done" and len(parts) in {2, 3}:
            result = service.checkin_block(
                parts[1],
                outcome="done",
                actual_minutes=int(parts[2]) if len(parts) == 3 else None,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action in {"block-skip", "block-defer"} and len(parts) in {2, 3}:
            if len(parts) == 3 and parts[2].lower() != "replan":
                return HELP
            result = service.checkin_block(
                parts[1],
                outcome="skipped" if action == "block-skip" else "deferred",
                propose_replan=len(parts) == 3,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action == "schedule-calendar-preview" and len(parts) == 2:
            return json.dumps(
                service.schedule_calendar_preview(parts[1]),
                ensure_ascii=False,
                indent=2,
            )
        if (
            action == "schedule-calendar-sync"
            and len(parts) == 3
            and parts[2].lower() == "confirm"
        ):
            return json.dumps(
                service.schedule_calendar_sync(parts[1], confirmed=True),
                ensure_ascii=False,
                indent=2,
            )
        if (
            action == "schedule-calendar-forget"
            and len(parts) == 3
            and parts[2].lower() == "confirm"
        ):
            return json.dumps(
                service.schedule_calendar_forget(
                    parts[1], confirmed_external_cleanup=True
                ),
                ensure_ascii=False,
                indent=2,
            )
        if action == "status":
            status = service.status()
            return f"Planner database: {status['database']}\nCounts: {status['counts']}"
        if action == "dispatch" and len(parts) == 1:
            return service.dispatch_due()
        if action == "automation-status" and len(parts) == 1:
            return json.dumps(automation_status(), ensure_ascii=False, indent=2)
        if action == "automation-enable" and len(parts) in {1, 2}:
            return json.dumps(
                ensure_automation_jobs(
                    delivery=parts[1] if len(parts) == 2 else None
                ),
                ensure_ascii=False,
                indent=2,
            )
        if action == "automation-pause" and len(parts) == 1:
            return json.dumps(
                pause_automation_jobs(), ensure_ascii=False, indent=2
            )
        return HELP
    except (PlannerError, ValueError) as exc:
        return f"Personal Planner: {exc}"
