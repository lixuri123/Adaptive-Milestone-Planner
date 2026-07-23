"""Single plugin-scoped model tool for structured Planner operations."""

from __future__ import annotations

import json
from typing import Any

from .cron_support import (
    automation_status,
    ensure_automation_jobs,
    ensure_radar_job,
    pause_automation_jobs,
)
from .expert_agents import route_domain_agents
from .service import PlannerError, PlannerService


def handle_personal_planner_tool(
    args: dict[str, Any],
    *,
    expert_runner: Any | None = None,
    service: PlannerService | None = None,
) -> str:
    service = service or PlannerService()
    action = str(args.get("action") or "").strip().lower()
    try:
        if action == "capture":
            result = service.capture(
                raw_content=args.get("raw_content") or "",
                title=args.get("title"),
                actionable_at=args.get("actionable_at"),
                due_at=args.get("due_at"),
                event_at=args.get("event_at"),
                discovered_at=args.get("discovered_at"),
                timezone_name=args.get("timezone"),
                importance=args.get("importance"),
                source_type=args.get("source_type") or "conversation",
                source_uri=args.get("source_uri"),
                event_duration_minutes=args.get("event_duration_minutes"),
            )
        elif action == "list":
            result = service.list(status=args.get("status"), limit=args.get("limit") or 50)
        elif action == "get":
            item = service.get(_required_id(args))
            result = {
                **item,
                "nudges": service.store.list_nudges(item["id"]),
                "revisions": service.revisions(item["id"]),
                "calendar_links": service.store.list_calendar_links(item["id"]),
            }
        elif action == "revisions":
            result = service.revisions(_required_id(args))
        elif action == "confirm":
            result = service.confirm(_required_id(args))
        elif action == "ack":
            result = service.acknowledge(_required_id(args))
        elif action == "snooze":
            until = args.get("snoozed_until")
            if not until:
                raise PlannerError("snoozed_until is required")
            result = service.snooze(_required_id(args), until)
        elif action == "done":
            result = service.complete(_required_id(args))
        elif action == "cancel":
            result = service.cancel(_required_id(args))
        elif action == "today":
            result = service.today()
        elif action == "event_record":
            event_type = str(args.get("event_type") or "").strip()
            if event_type not in {
                "daily.review_due",
                "checkin.review_due",
                "weekly.review_due",
            }:
                raise PlannerError("event_type is not an allowed review event")
            result = service.record_event(
                event_type=event_type,
                source=args.get("source_type") or "cron",
                dedupe_key=args.get("dedupe_key"),
                payload={"text": args.get("raw_content") or ""},
                occurred_at=args.get("discovered_at"),
            )
        elif action == "expert_prepare":
            event = service.record_event(
                event_type=args.get("event_type") or "work.requested",
                source=args.get("source_type") or "conversation",
                dedupe_key=args.get("dedupe_key"),
                payload={"text": args.get("raw_content") or ""},
                occurred_at=args.get("discovered_at"),
            )
            domains = args.get("expert_domains") or route_domain_agents(
                args.get("raw_content") or "",
                source_kind=args.get("source_type") or "conversation",
            )
            result = service.prepare_delegated_analysis(
                event["id"], domains=domains
            )
        elif action == "expert_ingest":
            result = service.ingest_delegated_analysis(
                str(args.get("event_id") or "").strip(),
                domain=str(args.get("expert_domain") or "").strip(),
                raw_json=str(args.get("expert_result_json") or ""),
            )
        elif action == "expert_analyze":
            if expert_runner is None:
                raise PlannerError("domain agent analysis is unavailable")
            event = service.record_event(
                event_type=args.get("event_type") or "work.requested",
                source=args.get("source_type") or "conversation",
                dedupe_key=args.get("dedupe_key"),
                payload={"text": args.get("raw_content") or ""},
                occurred_at=args.get("discovered_at"),
            )
            domains = args.get("expert_domains") or route_domain_agents(
                args.get("raw_content") or "",
                source_kind=args.get("source_type") or "conversation",
            )
            result = service.analyze_event(
                event["id"], domains=domains, expert_runner=expert_runner
            )
        elif action == "milestone_preview":
            result = service.milestone_preview(
                days=args.get("schedule_days"),
                start_at=args.get("schedule_start"),
                use_calendar=args.get("use_calendar_busy"),
                calendar_ids=args.get("calendar_ids"),
            )
        elif action == "milestone_confirm":
            result = service.confirm_milestone_plan(
                _required_plan_id(args),
                confirmed=bool(args.get("milestone_confirmed")),
                replace_plan_id=args.get("replace_plan_id"),
            )
        elif action == "milestone_reject":
            result = service.reject_milestone_plan(_required_plan_id(args))
        elif action == "milestone_checkin":
            result = service.milestone_checkin(
                _required_milestone_id(args),
                outcome=args.get("milestone_outcome") or "",
                actual_minutes=args.get("actual_minutes"),
                completion_fraction=args.get("completion_fraction"),
                note=args.get("progress_note"),
                propose_replan=bool(args.get("propose_replan", True)),
            )
        elif action == "milestone_snooze":
            result = service.milestone_snooze(
                _required_milestone_id(args),
                minutes=int(args.get("snooze_minutes") or 30),
            )
        elif action == "milestone_overview":
            result = service.milestone_overview()
        elif action == "source_add":
            result = service.add_gmail_source(
                name=args.get("source_name") or "",
                query=args.get("gmail_query") or "",
                importance=args.get("importance") or "normal",
                max_results=args.get("source_max_results") or 25,
            )
        elif action == "source_list":
            result = service.list_ingest_sources(
                enabled=(
                    bool(args.get("source_enabled"))
                    if "source_enabled" in args
                    else None
                )
            )
        elif action == "source_get":
            result = service.get_ingest_source(_required_source_id(args))
        elif action == "source_status":
            if "source_enabled" not in args:
                raise PlannerError("source_enabled is required")
            result = service.set_ingest_source_enabled(
                _required_source_id(args),
                enabled=bool(args.get("source_enabled")),
            )
        elif action == "source_scan":
            result = service.scan_ingest_sources(
                source_id=args.get("source_id"),
            )
        elif action == "profile_add":
            result = service.add_profile_fact(
                category=args.get("profile_category") or "",
                fact_key=args.get("profile_key") or "",
                fact_value=args.get("profile_value") or "",
                privacy=args.get("profile_privacy") or "standard",
                source_type=args.get("source_type") or "conversation",
                source_uri=args.get("source_uri"),
                confidence=args.get("profile_confidence", 1.0),
                valid_until=args.get("profile_valid_until"),
            )
        elif action == "profile_list":
            result = service.list_profile_facts(
                status=args.get("profile_status"),
            )
        elif action == "profile_get":
            result = service.get_profile_fact(_required_fact_id(args))
        elif action == "profile_confirm":
            result = service.confirm_profile_fact(_required_fact_id(args))
        elif action == "profile_reject":
            result = service.reject_profile_fact(_required_fact_id(args))
        elif action == "profile_revoke":
            result = service.revoke_profile_fact(_required_fact_id(args))
        elif action == "monitor_generate":
            result = service.generate_monitor_proposals()
        elif action == "monitor_list":
            result = service.list_monitor_proposals(
                status=args.get("monitor_status"),
            )
        elif action == "monitor_get":
            result = service.get_monitor_proposal(
                _required_monitor_id(args)
            )
        elif action == "monitor_approve":
            result = service.decide_monitor_proposal(
                _required_monitor_id(args),
                decision="approved",
            )
            try:
                ensured = ensure_radar_job()
                result["network_scan_active"] = bool(
                    ensured["job"].get("enabled", True)
                )
                result["automation_job_id"] = ensured["job"].get("id")
            except Exception as exc:
                result["network_scan_active"] = False
                result["automation_error"] = str(exc)
        elif action == "monitor_reject":
            result = service.decide_monitor_proposal(
                _required_monitor_id(args),
                decision="rejected",
            )
        elif action == "calendar_preview":
            result = service.calendar_preview(
                _required_id(args),
                calendar_id=args.get("calendar_id"),
                kinds=args.get("calendar_kinds"),
            )
        elif action == "calendar_sync":
            result = service.calendar_sync(
                _required_id(args),
                confirmed=bool(args.get("calendar_confirmed")),
                calendar_id=args.get("calendar_id"),
                kinds=args.get("calendar_kinds"),
            )
        elif action == "calendar_forget":
            result = service.calendar_forget(
                _required_id(args),
                confirmed_external_cleanup=bool(
                    args.get("calendar_external_cleanup_confirmed")
                ),
                calendar_id=args.get("calendar_id"),
                kinds=args.get("calendar_kinds"),
            )
        elif action == "goal_create":
            result = service.create_goal(
                title=args.get("title") or "",
                area=args.get("area") or "personal",
                outcome=args.get("outcome"),
                target_date=args.get("target_date"),
                weekly_target_minutes=args.get("weekly_target_minutes") or 0,
                priority=args.get("priority") or "normal",
            )
        elif action == "goal_list":
            result = service.list_goals(
                status=args.get("goal_status"), limit=args.get("limit") or 100
            )
        elif action == "goal_status":
            result = service.set_goal_status(
                _required_goal_id(args), args.get("goal_status") or ""
            )
        elif action == "task_create":
            result = service.create_task(
                title=args.get("title"),
                goal_id=args.get("goal_id"),
                planner_item_id=args.get("item_id"),
                notes=args.get("notes"),
                earliest_start=args.get("earliest_start"),
                due_at=args.get("due_at"),
                estimated_minutes=args.get("estimated_minutes"),
                min_block_minutes=args.get("min_block_minutes"),
                splittable=bool(args.get("splittable", True)),
                energy=args.get("energy") or "medium",
                priority=args.get("priority"),
            )
        elif action == "task_list":
            result = service.list_tasks(
                status=args.get("task_status"),
                goal_id=args.get("goal_id"),
                limit=args.get("limit") or 200,
            )
        elif action == "task_update":
            changes = {
                key: args[key]
                for key in (
                    "title",
                    "notes",
                    "earliest_start",
                    "due_at",
                    "estimated_minutes",
                    "min_block_minutes",
                    "splittable",
                    "energy",
                    "priority",
                )
                if key in args
            }
            result = service.update_task(_required_task_id(args), changes)
        elif action == "task_progress":
            result = service.add_task_progress(
                _required_task_id(args),
                args.get("progress_minutes"),
                note=args.get("progress_note"),
            )
        elif action == "task_done":
            result = service.complete_task(
                _required_task_id(args), note=args.get("progress_note")
            )
        elif action == "task_cancel":
            result = service.cancel_task(_required_task_id(args))
        elif action == "availability_add":
            result = service.add_availability(
                weekday=args.get("weekday"),
                start_time=args.get("start_time") or "",
                end_time=args.get("end_time") or "",
                energy=args.get("energy") or "medium",
                label=args.get("label"),
            )
        elif action == "availability_list":
            result = service.list_availability(weekday=args.get("weekday"))
        elif action == "availability_clear":
            result = service.clear_availability(
                availability_id=args.get("availability_id"),
                weekday=args.get("weekday"),
            )
        elif action == "schedule_preview":
            result = service.schedule_preview(
                days=args.get("schedule_days"),
                start_at=args.get("schedule_start"),
                replan=bool(args.get("replan")),
                use_calendar=args.get("use_calendar_busy"),
                calendar_ids=args.get("calendar_ids"),
            )
        elif action == "schedule_get":
            result = service.get_schedule(_required_plan_id(args))
        elif action == "schedule_list":
            result = service.list_schedules(limit=args.get("limit") or 50)
        elif action == "schedule_confirm":
            result = service.confirm_schedule(
                _required_plan_id(args),
                confirmed=bool(args.get("schedule_confirmed")),
                replace=bool(args.get("replace_existing_schedule")),
            )
        elif action == "schedule_cancel":
            result = service.cancel_schedule(_required_plan_id(args))
        elif action == "block_get":
            result = service.get_block(_required_block_id(args))
        elif action == "block_checkin":
            result = service.checkin_block(
                _required_block_id(args),
                outcome=args.get("block_outcome") or "",
                actual_minutes=args.get("actual_minutes"),
                note=args.get("progress_note"),
                propose_replan=bool(args.get("propose_replan")),
            )
        elif action == "schedule_calendar_preview":
            result = service.schedule_calendar_preview(
                _required_plan_id(args),
                calendar_id=args.get("calendar_id"),
            )
        elif action == "schedule_calendar_sync":
            result = service.schedule_calendar_sync(
                _required_plan_id(args),
                confirmed=bool(args.get("schedule_calendar_confirmed")),
                calendar_id=args.get("calendar_id"),
            )
        elif action == "schedule_calendar_forget":
            result = service.schedule_calendar_forget(
                _required_plan_id(args),
                confirmed_external_cleanup=bool(
                    args.get("calendar_external_cleanup_confirmed")
                ),
                calendar_id=args.get("calendar_id"),
            )
        elif action == "calendar_busy":
            result = service.calendar_busy(
                start_at=args.get("schedule_start"),
                end_at=args.get("schedule_end"),
                calendar_ids=args.get("calendar_ids"),
            )
        elif action == "daily_review":
            result = service.daily_review(
                review_date=args.get("review_date"),
                use_calendar=args.get("use_calendar_busy"),
                calendar_ids=args.get("calendar_ids"),
                propose_replan=bool(args.get("propose_replan")),
            )
        elif action == "automation_status":
            result = automation_status()
        elif action == "automation_enable":
            result = ensure_automation_jobs(
                delivery=args.get("automation_delivery")
            )
        elif action == "automation_pause":
            result = pause_automation_jobs()
        elif action == "status":
            result = service.status()
        else:
            raise PlannerError(
                "action must be capture, list, get, revisions, confirm, ack, "
                "snooze, done, cancel, today, source_add, source_list, source_get, "
                "source_status, source_scan, profile_add, profile_list, "
                "profile_get, profile_confirm, profile_reject, "
                "profile_revoke, monitor_generate, monitor_list, "
                "monitor_get, monitor_approve, monitor_reject, "
                "calendar_preview, calendar_sync, "
                "calendar_forget, goal_create, goal_list, goal_status, "
                "task_create, task_update, task_list, task_progress, task_done, "
                "task_cancel, "
                "availability_add, availability_list, availability_clear, "
                "schedule_preview, schedule_get, schedule_list, "
                "schedule_confirm, schedule_cancel, block_get, block_checkin, "
                "schedule_calendar_preview, schedule_calendar_sync, "
                "schedule_calendar_forget, calendar_busy, daily_review, "
                "automation_status, automation_enable, automation_pause, or status"
            )
        return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
    except (PlannerError, ValueError, RuntimeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def _required_id(args: dict[str, Any]) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise PlannerError("item_id is required")
    return item_id


def _required_goal_id(args: dict[str, Any]) -> str:
    goal_id = str(args.get("goal_id") or "").strip()
    if not goal_id:
        raise PlannerError("goal_id is required")
    return goal_id


def _required_task_id(args: dict[str, Any]) -> str:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise PlannerError("task_id is required")
    return task_id


def _required_plan_id(args: dict[str, Any]) -> str:
    plan_id = str(args.get("plan_id") or "").strip()
    if not plan_id:
        raise PlannerError("plan_id is required")
    return plan_id


def _required_block_id(args: dict[str, Any]) -> str:
    block_id = str(args.get("block_id") or "").strip()
    if not block_id:
        raise PlannerError("block_id is required")
    return block_id


def _required_milestone_id(args: dict[str, Any]) -> str:
    milestone_id = str(args.get("milestone_id") or "").strip()
    if not milestone_id:
        raise PlannerError("milestone_id is required")
    return milestone_id


def _required_source_id(args: dict[str, Any]) -> str:
    source_id = str(args.get("source_id") or "").strip()
    if not source_id:
        raise PlannerError("source_id is required")
    return source_id


def _required_fact_id(args: dict[str, Any]) -> str:
    fact_id = str(args.get("fact_id") or "").strip()
    if not fact_id:
        raise PlannerError("fact_id is required")
    return fact_id


def _required_monitor_id(args: dict[str, Any]) -> str:
    monitor_id = str(args.get("monitor_id") or "").strip()
    if not monitor_id:
        raise PlannerError("monitor_id is required")
    return monitor_id


PERSONAL_PLANNER_SCHEMA = {
    "name": "personal_planner",
    "description": (
        "Manage durable personal commitments and acknowledgement-aware reminders. "
        "Use capture to save a notification as a DRAFT after extracting its distinct "
        "discovered/actionable/deadline/event times. Show the extracted draft to the "
        "user before calling confirm. Never invent a precise date when the source is "
        "ambiguous. Acknowledging a reminder does not complete the commitment; call "
        "done only after the user says the action is complete. Times must be ISO 8601."
        " Calendar writes require a calendar_preview followed by calendar_sync with "
        "calendar_confirmed=true after the user explicitly approves the exact events."
        " Long-term planning uses goals, tasks, weekly availability, and a draft "
        "schedule_preview. Never confirm a rolling schedule until the user reviews "
        "its blocks and explicitly approves it. A replan also requires explicit "
        "approval to replace the previous confirmed schedule. Calendar busy reads "
        "are read-only and may fall back to visibly marked cached data. Use "
        "daily_review to identify missed blocks, calendar conflicts, and uncovered "
        "near-term deadlines before proposing a replan draft. A calendar-backed "
        "schedule is re-read and fingerprinted again at confirmation; never bypass "
        "a changed, unavailable, or expired draft. Confirmed blocks may be checked "
        "in as done, skipped, or deferred. Only done credits actual effort. Plan "
        "block calendar writes require schedule_calendar_preview followed by "
        "schedule_calendar_sync with explicit approval; never delete or rewrite "
        "external events automatically after a block changes."
        " Read-only Gmail sources may be configured to scan matching messages "
        "into Planner drafts. Source scans never infer dates, confirm a draft, "
        "schedule reminders, or write calendar events; the user must review "
        "each captured notice explicitly."
        " Profile facts are proposed first and must be explicitly confirmed "
        "before they influence monitor_generate. When a user asks to record, "
        "save, or build a personal profile/background for planning, use "
        "profile_add instead of ordinary conversation memory. If the user says "
        "to confirm before activation, profile_add is still the safe first step: "
        "it creates only a proposed fact, so call it immediately and ask the user "
        "to confirm the visible proposal rather than withholding the tool call. "
        "Sensitive facts are excluded "
        "from automatic proposal generation. Approving a monitor proposal "
        "enables the shared Planner information-radar Cron job. That job may "
        "search only within approved proposal terms/source kinds and must save "
        "results as review-only drafts; it never auto-confirms or writes calendars."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "capture", "list", "get", "revisions", "confirm", "ack",
                    "snooze", "done", "cancel", "today", "source_add", "source_list",
                    "source_get", "source_status", "source_scan",
                    "profile_add", "profile_list", "profile_get",
                    "profile_confirm", "profile_reject", "profile_revoke",
                    "monitor_generate", "monitor_list", "monitor_get",
                    "monitor_approve", "monitor_reject", "calendar_preview",
                    "calendar_sync", "calendar_forget", "goal_create",
                    "goal_list", "goal_status", "task_create", "task_update", "task_list",
                    "task_progress", "task_done", "task_cancel", "availability_add",
                    "availability_list", "availability_clear",
                    "schedule_preview", "schedule_get", "schedule_list",
                    "schedule_confirm", "schedule_cancel", "block_get", "block_checkin",
                    "schedule_calendar_preview", "schedule_calendar_sync",
                    "schedule_calendar_forget", "calendar_busy",
                    "daily_review", "automation_status", "automation_enable",
                    "automation_pause", "status", "event_record", "expert_prepare",
                    "expert_ingest", "expert_analyze", "milestone_preview",
                    "milestone_confirm", "milestone_reject",
                    "milestone_checkin", "milestone_snooze",
                    "milestone_overview"
                ],
            },
            "item_id": {"type": "string", "description": "Planner item ID such as P-A1B2C3D4"},
            "raw_content": {"type": "string", "description": "Original notification text for capture"},
            "title": {"type": "string"},
            "discovered_at": {"type": "string", "description": "When the notice was discovered"},
            "actionable_at": {"type": "string", "description": "When action first becomes possible"},
            "due_at": {"type": "string", "description": "Latest safe completion time"},
            "event_at": {"type": "string", "description": "Actual event time, distinct from its deadline"},
            "snoozed_until": {"type": "string", "description": "Future ISO 8601 timestamp"},
            "timezone": {"type": "string", "description": "IANA timezone, defaults to profile config"},
            "importance": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
            "source_type": {"type": "string"},
            "source_uri": {"type": "string"},
            "event_type": {"type": "string"},
            "dedupe_key": {"type": "string"},
            "event_id": {
                "type": "string",
                "description": "Planner event ID returned by expert_prepare",
            },
            "expert_domains": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["research", "career", "communication"],
                },
            },
            "expert_domain": {
                "type": "string",
                "enum": ["research", "career", "communication"],
            },
            "expert_result_json": {
                "type": "string",
                "description": "Exactly one delegated WorkPackage JSON object",
            },
            "event_duration_minutes": {
                "type": "integer",
                "minimum": 5,
                "maximum": 1440,
            },
            "goal_id": {"type": "string", "description": "Goal ID such as G-A1B2C3D4"},
            "task_id": {"type": "string", "description": "Task ID such as T-A1B2C3D4"},
            "plan_id": {"type": "string", "description": "Schedule ID such as S-A1B2C3D4"},
            "replace_plan_id": {
                "type": "string",
                "description": "Confirmed MP-ID that a reviewed draft replaces",
            },
            "milestone_id": {
                "type": "string",
                "description": "Daily milestone ID such as DM-A1B2C3D4",
            },
            "block_id": {"type": "string", "description": "Plan block ID such as B-A1B2C3D4"},
            "source_id": {
                "type": "string",
                "description": "Ingestion source ID such as F-A1B2C3D4",
            },
            "source_name": {
                "type": "string",
                "description": "Short user-facing name for a Gmail source",
            },
            "gmail_query": {
                "type": "string",
                "description": (
                    "Gmail search query. Prefer a bounded query such as "
                    "from:school.edu newer_than:30d"
                ),
            },
            "source_enabled": {
                "type": "boolean",
                "description": (
                    "Enable/disable a source, or filter source_list when supplied"
                ),
            },
            "source_max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
            },
            "fact_id": {
                "type": "string",
                "description": "Profile fact ID such as PF-A1B2C3D4",
            },
            "profile_category": {
                "type": "string",
                "description": "Fact category such as education or career",
            },
            "profile_key": {
                "type": "string",
                "description": "Fact key such as school, degree, or field",
            },
            "profile_value": {
                "type": "string",
                "description": "User-reviewable fact value",
            },
            "profile_privacy": {
                "type": "string",
                "enum": ["standard", "sensitive"],
                "description": (
                    "Sensitive facts are stored locally but excluded from "
                    "automatic monitoring proposals"
                ),
            },
            "profile_confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "profile_valid_until": {
                "type": "string",
                "description": "Optional ISO 8601 expiry for a profile fact",
            },
            "profile_status": {
                "type": "string",
                "enum": [
                    "proposed",
                    "confirmed",
                    "rejected",
                    "revoked",
                    "superseded",
                ],
            },
            "monitor_id": {
                "type": "string",
                "description": "Monitoring proposal ID such as MP-A1B2C3D4",
            },
            "monitor_status": {
                "type": "string",
                "enum": [
                    "proposed",
                    "approved",
                    "rejected",
                    "superseded",
                ],
            },
            "area": {
                "type": "string",
                "description": "Goal area such as coursework, research, career, or personal",
            },
            "outcome": {"type": "string", "description": "Observable goal outcome"},
            "target_date": {"type": "string", "description": "Long-term goal target date"},
            "weekly_target_minutes": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10080,
            },
            "goal_status": {
                "type": "string",
                "enum": ["active", "paused", "done", "cancelled"],
            },
            "notes": {"type": "string"},
            "earliest_start": {"type": "string"},
            "estimated_minutes": {
                "type": "integer",
                "minimum": 5,
                "maximum": 43200,
            },
            "min_block_minutes": {
                "type": "integer",
                "minimum": 5,
                "maximum": 480,
            },
            "splittable": {"type": "boolean"},
            "energy": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high", "critical"],
            },
            "task_status": {
                "type": "string",
                "enum": ["backlog", "planned", "done", "cancelled"],
            },
            "progress_minutes": {"type": "integer", "minimum": 1},
            "progress_note": {"type": "string"},
            "block_outcome": {
                "type": "string",
                "enum": ["done", "skipped", "deferred"],
            },
            "actual_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1440,
                "description": "Actual effort for a done block only",
            },
            "milestone_outcome": {
                "type": "string",
                "enum": ["done", "partial", "not_started", "blocked", "skipped"],
            },
            "completion_fraction": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1,
            },
            "snooze_minutes": {
                "type": "integer",
                "minimum": 5,
                "maximum": 1440,
            },
            "weekday": {
                "type": "string",
                "description": "Recurring weekday as 0-6 or mon-sun",
            },
            "start_time": {"type": "string", "description": "HH:MM"},
            "end_time": {"type": "string", "description": "HH:MM"},
            "label": {"type": "string"},
            "availability_id": {"type": "string"},
            "schedule_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 42,
            },
            "schedule_start": {"type": "string", "description": "Optional ISO start"},
            "schedule_end": {"type": "string", "description": "Optional ISO end"},
            "replan": {
                "type": "boolean",
                "description": "Reallocate unfinished work from overlapping confirmed plans",
            },
            "schedule_confirmed": {
                "type": "boolean",
                "description": "Must be true only after the exact draft schedule was reviewed",
            },
            "milestone_confirmed": {
                "type": "boolean",
                "description": (
                    "Must be true only after the exact milestone draft was reviewed"
                ),
            },
            "schedule_calendar_confirmed": {
                "type": "boolean",
                "description": (
                    "Must be true only after the exact plan-block calendar "
                    "event preview was explicitly approved"
                ),
            },
            "replace_existing_schedule": {
                "type": "boolean",
                "description": "Required to confirm a replan that supersedes confirmed blocks",
            },
            "use_calendar_busy": {
                "type": "boolean",
                "description": (
                    "When true, read configured Google calendars and subtract "
                    "their events from planning availability"
                ),
            },
            "calendar_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Google Calendar ids used as busy-time sources",
            },
            "review_date": {
                "type": "string",
                "description": "Local review date, normally YYYY-MM-DD",
            },
            "propose_replan": {
                "type": "boolean",
                "description": (
                    "Create a local replan draft only when daily_review finds "
                    "missed work, conflicts, or deadline capacity gaps"
                ),
            },
            "calendar_id": {
                "type": "string",
                "description": "Google Calendar id; defaults to primary",
            },
            "calendar_kinds": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["actionable", "deadline", "event"],
                },
            },
            "calendar_confirmed": {
                "type": "boolean",
                "description": (
                    "Must be true for calendar_sync and only after the user "
                    "explicitly approved the calendar_preview"
                ),
            },
            "calendar_external_cleanup_confirmed": {
                "type": "boolean",
                "description": (
                    "Required for calendar_forget. Confirms the user reviewed "
                    "and reconciled the linked external calendar events."
                ),
            },
            "automation_delivery": {
                "type": "string",
                "description": (
                    "Optional Cron delivery target such as local, telegram, or origin"
                ),
            },
            "status": {"type": "string", "enum": ["draft", "confirmed", "done", "cancelled"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["action"],
    },
}
