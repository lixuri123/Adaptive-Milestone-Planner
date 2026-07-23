"""``hermes plan`` command surface."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .cron_support import (
    ensure_dispatch_job,
    ensure_ingest_job,
    ensure_review_job,
)
from .service import PlannerError, PlannerService


def register_cli(parser: argparse.ArgumentParser) -> None:
    subs = parser.add_subparsers(dest="planner_action")

    capture = subs.add_parser("capture", help="Capture a notification as a draft")
    capture.add_argument("text", nargs="+", help="Raw notification text")
    capture.add_argument("--title", default=None)
    capture.add_argument("--actionable-at", default=None)
    capture.add_argument("--due-at", default=None)
    capture.add_argument("--event-at", default=None)
    capture.add_argument("--timezone", default=None)
    capture.add_argument(
        "--importance", choices=("low", "normal", "high", "critical"), default=None
    )
    capture.add_argument("--source-type", default="manual")
    capture.add_argument("--source-uri", default=None)
    capture.add_argument("--event-duration-minutes", type=int, default=None)
    capture.add_argument("--confirm", action="store_true")

    inbox = subs.add_parser("inbox", aliases=["list", "ls"], help="List Planner items")
    inbox.add_argument("--status", default=None)
    inbox.add_argument("--limit", type=int, default=50)

    show = subs.add_parser("show", help="Show one Planner item")
    show.add_argument("item_id")

    revisions = subs.add_parser("revisions", help="Show notification revision history")
    revisions.add_argument("item_id")

    confirm = subs.add_parser("confirm", help="Confirm a draft and schedule reminders")
    confirm.add_argument("item_id")

    ack = subs.add_parser("ack", help="Acknowledge delivered reminders")
    ack.add_argument("item_id")

    snooze = subs.add_parser("snooze", help="Snooze an item until an ISO timestamp")
    snooze.add_argument("item_id")
    snooze.add_argument("until")

    done = subs.add_parser("done", help="Complete an item and cancel pending reminders")
    done.add_argument("item_id")

    subs.add_parser("today", help="List commitments with a time point today")
    subs.add_parser("dispatch", help="Claim and print due reminders")
    subs.add_parser("status", help="Show Planner database and queue status")

    source_add = subs.add_parser(
        "source-add",
        help="Add a read-only Gmail query that creates Planner drafts",
    )
    source_add.add_argument("name")
    source_add.add_argument("--query", required=True)
    source_add.add_argument(
        "--importance",
        choices=("low", "normal", "high", "critical"),
        default="normal",
    )
    source_add.add_argument("--max-results", type=int, default=25)

    sources = subs.add_parser(
        "sources",
        aliases=["source-list"],
        help="List notification ingestion sources",
    )
    sources.add_argument("--enabled-only", action="store_true")

    source_show = subs.add_parser(
        "source-show",
        help="Show an ingestion source and recent receipts",
    )
    source_show.add_argument("source_id")

    source_enable = subs.add_parser(
        "source-enable",
        help="Enable an ingestion source",
    )
    source_enable.add_argument("source_id")

    source_disable = subs.add_parser(
        "source-disable",
        help="Disable an ingestion source without deleting its history",
    )
    source_disable.add_argument("source_id")

    source_scan = subs.add_parser(
        "source-scan",
        help="Run a read-only Gmail scan now",
    )
    source_scan.add_argument("source_id", nargs="?", default=None)

    profile_add = subs.add_parser(
        "profile-add",
        help="Add a proposed structured profile fact",
    )
    profile_add.add_argument("category")
    profile_add.add_argument("fact_key")
    profile_add.add_argument("fact_value", nargs="+")
    profile_add.add_argument(
        "--privacy",
        choices=("standard", "sensitive"),
        default="standard",
    )
    profile_add.add_argument("--source-type", default="user")
    profile_add.add_argument("--source-uri", default=None)
    profile_add.add_argument("--confidence", type=float, default=1.0)
    profile_add.add_argument("--valid-until", default=None)

    profile = subs.add_parser(
        "profile",
        aliases=["profile-list"],
        help="List structured profile facts",
    )
    profile.add_argument("--status", default=None)

    profile_show = subs.add_parser(
        "profile-show",
        help="Show one profile fact",
    )
    profile_show.add_argument("fact_id")

    profile_confirm = subs.add_parser(
        "profile-confirm",
        help="Confirm a proposed profile fact",
    )
    profile_confirm.add_argument("fact_id")

    profile_reject = subs.add_parser(
        "profile-reject",
        help="Reject a proposed profile fact",
    )
    profile_reject.add_argument("fact_id")

    profile_revoke = subs.add_parser(
        "profile-revoke",
        help="Revoke a previously confirmed profile fact",
    )
    profile_revoke.add_argument("fact_id")

    subs.add_parser(
        "radar-propose",
        help="Generate explainable monitoring proposals without scanning",
    )

    monitors = subs.add_parser(
        "monitors",
        aliases=["monitor-list"],
        help="List profile-derived monitoring proposals",
    )
    monitors.add_argument("--status", default=None)

    monitor_show = subs.add_parser(
        "monitor-show",
        help="Show one monitoring proposal",
    )
    monitor_show.add_argument("proposal_id")

    monitor_approve = subs.add_parser(
        "monitor-approve",
        help="Approve a proposal without starting network scanning",
    )
    monitor_approve.add_argument("proposal_id")

    monitor_reject = subs.add_parser(
        "monitor-reject",
        help="Reject a monitoring proposal",
    )
    monitor_reject.add_argument("proposal_id")

    calendar_preview = subs.add_parser(
        "calendar-preview", help="Preview calendar events without writing"
    )
    calendar_preview.add_argument("item_id")
    calendar_preview.add_argument("--calendar", default=None)
    calendar_preview.add_argument(
        "--kinds", default=None, help="Comma-separated: actionable,deadline,event"
    )

    calendar_sync = subs.add_parser(
        "calendar-sync", help="Create previewed Google Calendar events"
    )
    calendar_sync.add_argument("item_id")
    calendar_sync.add_argument("--calendar", default=None)
    calendar_sync.add_argument(
        "--kinds", default=None, help="Comma-separated: actionable,deadline,event"
    )
    calendar_sync.add_argument(
        "--confirm",
        action="store_true",
        help="Required acknowledgement that the calendar write was reviewed",
    )

    calendar_forget = subs.add_parser(
        "calendar-forget",
        help="Forget links after external events were manually reconciled",
    )
    calendar_forget.add_argument("item_id")
    calendar_forget.add_argument("--calendar", default=None)
    calendar_forget.add_argument("--kinds", default=None)
    calendar_forget.add_argument(
        "--confirm-external-cleanup",
        action="store_true",
        help="Required: confirms external events were reviewed and reconciled",
    )

    goal_add = subs.add_parser("goal-add", help="Create a long-term goal")
    goal_add.add_argument("title", nargs="+")
    goal_add.add_argument("--area", default="personal")
    goal_add.add_argument("--outcome", default=None)
    goal_add.add_argument("--target-date", default=None)
    goal_add.add_argument("--weekly-minutes", type=int, default=0)
    goal_add.add_argument(
        "--priority", choices=("low", "normal", "high", "critical"), default="normal"
    )

    goals = subs.add_parser("goals", aliases=["goal-list"], help="List long-term goals")
    goals.add_argument("--status", default=None)
    goals.add_argument("--limit", type=int, default=100)

    goal_status = subs.add_parser("goal-status", help="Change a goal's status")
    goal_status.add_argument("goal_id")
    goal_status.add_argument(
        "status", choices=("active", "paused", "done", "cancelled")
    )

    task_add = subs.add_parser("task-add", help="Create schedulable work")
    task_add.add_argument("title", nargs="*")
    task_add.add_argument("--goal", default=None)
    task_add.add_argument("--item", default=None)
    task_add.add_argument("--notes", default=None)
    task_add.add_argument("--earliest", default=None)
    task_add.add_argument("--due", default=None)
    task_add.add_argument("--estimate", type=int, default=None)
    task_add.add_argument("--min-block", type=int, default=None)
    task_add.add_argument("--not-splittable", action="store_true")
    task_add.add_argument(
        "--energy", choices=("low", "medium", "high"), default="medium"
    )
    task_add.add_argument(
        "--priority", choices=("low", "normal", "high", "critical"), default=None
    )

    tasks = subs.add_parser("tasks", aliases=["task-list"], help="List schedulable work")
    tasks.add_argument("--status", default=None)
    tasks.add_argument("--goal", default=None)
    tasks.add_argument("--limit", type=int, default=200)

    task_progress = subs.add_parser("task-progress", help="Record completed work")
    task_progress.add_argument("task_id")
    task_progress.add_argument("minutes", type=int)
    task_progress.add_argument("--note", default=None)

    task_done = subs.add_parser("task-done", help="Complete a planning task")
    task_done.add_argument("task_id")
    task_done.add_argument("--note", default=None)

    availability_add = subs.add_parser(
        "availability-add", help="Add a recurring weekly planning window"
    )
    availability_add.add_argument("weekday")
    availability_add.add_argument("start_time")
    availability_add.add_argument("end_time")
    availability_add.add_argument(
        "--energy", choices=("low", "medium", "high"), default="medium"
    )
    availability_add.add_argument("--label", default=None)

    availability = subs.add_parser(
        "availability", help="List recurring weekly planning windows"
    )
    availability.add_argument("--weekday", default=None)

    availability_clear = subs.add_parser(
        "availability-clear", help="Remove one window or a weekday's windows"
    )
    availability_clear.add_argument("--id", dest="availability_id", default=None)
    availability_clear.add_argument("--weekday", default=None)

    milestone_preview = subs.add_parser(
        "milestone-preview", help="Create a draft day-level milestone plan"
    )
    milestone_preview.add_argument("--days", type=int, default=None)
    milestone_preview.add_argument("--start", default=None)
    milestone_calendar = milestone_preview.add_mutually_exclusive_group()
    milestone_calendar.add_argument(
        "--calendar", dest="calendar_busy", action="store_true"
    )
    milestone_calendar.add_argument(
        "--no-calendar", dest="calendar_busy", action="store_false"
    )
    milestone_preview.set_defaults(calendar_busy=None)

    subs.add_parser(
        "milestone-overview", help="Show active milestone state and remaining work"
    )

    milestone_show = subs.add_parser(
        "milestone-show", help="Show one milestone plan"
    )
    milestone_show.add_argument("plan_id")

    milestone_confirm = subs.add_parser(
        "milestone-confirm", help="Confirm an explicitly reviewed milestone draft"
    )
    milestone_confirm.add_argument("plan_id")
    milestone_confirm.add_argument("--confirm", action="store_true")
    milestone_confirm.add_argument(
        "--replace", default=None, help="Exact confirmed MP-ID being replaced"
    )

    milestone_reject = subs.add_parser(
        "milestone-reject", help="Reject a milestone draft"
    )
    milestone_reject.add_argument("plan_id")

    milestone_checkin = subs.add_parser(
        "milestone-checkin", help="Record outcome for one daily milestone"
    )
    milestone_checkin.add_argument("milestone_id")
    milestone_checkin.add_argument(
        "--outcome",
        required=True,
        choices=("done", "partial", "not_started", "blocked", "skipped"),
    )
    milestone_checkin.add_argument("--minutes", type=int, default=None)
    milestone_checkin.add_argument("--fraction", type=float, default=None)
    milestone_checkin.add_argument("--note", default=None)

    milestone_snooze = subs.add_parser(
        "milestone-snooze", help="Delay one unfinished milestone reminder"
    )
    milestone_snooze.add_argument("milestone_id")
    milestone_snooze.add_argument("--minutes", type=int, default=30)

    schedule_preview = subs.add_parser(
        "schedule-preview", help="Create a draft rolling schedule"
    )
    schedule_preview.add_argument("--days", type=int, default=None)
    schedule_preview.add_argument("--start", default=None)
    schedule_preview.add_argument(
        "--replan",
        action="store_true",
        help="Reallocate unfinished work from overlapping confirmed plans",
    )
    calendar_group = schedule_preview.add_mutually_exclusive_group()
    calendar_group.add_argument(
        "--calendar-busy",
        dest="calendar_busy",
        action="store_true",
        help="Read configured Google calendars as busy time",
    )
    calendar_group.add_argument(
        "--no-calendar-busy",
        dest="calendar_busy",
        action="store_false",
        help="Ignore external calendars for this preview",
    )
    schedule_preview.set_defaults(calendar_busy=None)
    schedule_preview.add_argument(
        "--calendar-ids",
        default=None,
        help="Comma-separated Google Calendar ids; defaults to plugin config",
    )

    schedule_show = subs.add_parser("schedule-show", help="Show one rolling schedule")
    schedule_show.add_argument("plan_id")

    schedule_list = subs.add_parser("schedule-list", help="List rolling schedules")
    schedule_list.add_argument("--limit", type=int, default=50)

    schedule_confirm = subs.add_parser(
        "schedule-confirm", help="Confirm a reviewed rolling schedule"
    )
    schedule_confirm.add_argument("plan_id")
    schedule_confirm.add_argument("--confirm", action="store_true")
    schedule_confirm.add_argument(
        "--replace",
        action="store_true",
        help="Required when confirming a replan that supersedes an old plan",
    )

    block_show = subs.add_parser("block-show", help="Show one plan block")
    block_show.add_argument("block_id")

    block_done = subs.add_parser(
        "block-done", help="Complete a plan block and record actual effort"
    )
    block_done.add_argument("block_id")
    block_done.add_argument("--actual-minutes", type=int, default=None)
    block_done.add_argument("--note", default=None)

    block_skip = subs.add_parser(
        "block-skip", help="Skip a plan block without recording progress"
    )
    block_skip.add_argument("block_id")
    block_skip.add_argument("--note", default=None)
    block_skip.add_argument("--propose-replan", action="store_true")

    block_defer = subs.add_parser(
        "block-defer", help="Defer a plan block and release its reserved time"
    )
    block_defer.add_argument("block_id")
    block_defer.add_argument("--note", default=None)
    block_defer.add_argument("--propose-replan", action="store_true")

    schedule_calendar_preview = subs.add_parser(
        "schedule-calendar-preview",
        help="Preview confirmed plan blocks as Google Calendar events",
    )
    schedule_calendar_preview.add_argument("plan_id")
    schedule_calendar_preview.add_argument("--calendar", default=None)

    schedule_calendar_sync = subs.add_parser(
        "schedule-calendar-sync",
        help="Create previewed plan-block Google Calendar events",
    )
    schedule_calendar_sync.add_argument("plan_id")
    schedule_calendar_sync.add_argument("--calendar", default=None)
    schedule_calendar_sync.add_argument("--confirm", action="store_true")

    schedule_calendar_forget = subs.add_parser(
        "schedule-calendar-forget",
        help="Forget plan-block links after external events were reconciled",
    )
    schedule_calendar_forget.add_argument("plan_id")
    schedule_calendar_forget.add_argument("--calendar", default=None)
    schedule_calendar_forget.add_argument(
        "--confirm-external-cleanup", action="store_true"
    )

    calendar_busy = subs.add_parser(
        "calendar-busy", help="Read and inspect external Google Calendar busy time"
    )
    calendar_busy.add_argument("--start", default=None)
    calendar_busy.add_argument("--end", default=None)
    calendar_busy.add_argument("--calendar-ids", default=None)

    review = subs.add_parser(
        "review", help="Build a daily progress, conflict, and deadline review"
    )
    review.add_argument("--date", default=None)
    review.add_argument("--propose-replan", action="store_true")
    review_calendar = review.add_mutually_exclusive_group()
    review_calendar.add_argument(
        "--calendar-busy", dest="calendar_busy", action="store_true"
    )
    review_calendar.add_argument(
        "--no-calendar-busy", dest="calendar_busy", action="store_false"
    )
    review.set_defaults(calendar_busy=None)
    review.add_argument("--calendar-ids", default=None)

    review_setup = subs.add_parser(
        "review-setup", help="Install or update the daily review Cron job"
    )
    review_setup.add_argument("--schedule", default=None)
    review_setup.add_argument("--delivery", default=None)

    ingest_setup = subs.add_parser(
        "ingest-setup",
        help="Install or update the automatic inbox scan Cron job",
    )
    ingest_setup.add_argument("--schedule", default=None)
    ingest_setup.add_argument("--delivery", default=None)

    setup = subs.add_parser("setup", help="Install or update the Cron dispatch job")
    setup.add_argument("--schedule", default=None, help="Default: config or every 10m")
    setup.add_argument("--delivery", default=None, help="Cron delivery target")

    parser.set_defaults(func=planner_command)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def planner_command(args: argparse.Namespace) -> int:
    action = getattr(args, "planner_action", None)
    if not action:
        print(
            "usage: hermes plan "
            "{capture,inbox,show,revisions,confirm,ack,snooze,done,today,"
            "source-add,sources,source-show,source-enable,source-disable,"
            "source-scan,ingest-setup,"
            "profile-add,profile,profile-show,profile-confirm,profile-reject,"
            "profile-revoke,radar-propose,monitors,monitor-show,"
            "monitor-approve,monitor-reject,"
            "calendar-preview,calendar-sync,calendar-forget,goal-add,goals,"
            "goal-status,task-add,tasks,task-progress,task-done,"
            "availability-add,availability,availability-clear,schedule-preview,"
            "milestone-preview,milestone-overview,milestone-show,"
            "milestone-confirm,milestone-reject,milestone-checkin,milestone-snooze,"
            "schedule-show,schedule-list,schedule-confirm,block-show,block-done,"
            "block-skip,block-defer,schedule-calendar-preview,"
            "schedule-calendar-sync,schedule-calendar-forget,calendar-busy,"
            "review,review-setup,dispatch,status,setup}"
        )
        return 2

    service = PlannerService()
    try:
        if action == "capture":
            item = service.capture(
                raw_content=" ".join(args.text),
                title=args.title,
                actionable_at=args.actionable_at,
                due_at=args.due_at,
                event_at=args.event_at,
                timezone_name=args.timezone,
                importance=args.importance,
                source_type=args.source_type,
                source_uri=args.source_uri,
                event_duration_minutes=args.event_duration_minutes,
            )
            _print(service.confirm(item["id"]) if args.confirm else item)
        elif action in {"inbox", "list", "ls"}:
            _print(service.list(status=args.status, limit=args.limit))
        elif action == "show":
            item = service.get(args.item_id)
            _print(
                {
                    **item,
                    "nudges": service.store.list_nudges(item["id"]),
                    "revisions": service.revisions(item["id"]),
                    "calendar_links": service.store.list_calendar_links(item["id"]),
                }
            )
        elif action == "revisions":
            _print(service.revisions(args.item_id))
        elif action == "confirm":
            _print(service.confirm(args.item_id))
        elif action == "ack":
            _print(service.acknowledge(args.item_id))
        elif action == "snooze":
            _print(service.snooze(args.item_id, args.until))
        elif action == "done":
            _print(service.complete(args.item_id))
        elif action == "today":
            _print(service.today())
        elif action == "dispatch":
            message = service.dispatch_due()
            if message:
                print(message)
        elif action == "status":
            _print(service.status())
        elif action == "source-add":
            _print(
                service.add_gmail_source(
                    name=args.name,
                    query=args.query,
                    importance=args.importance,
                    max_results=args.max_results,
                )
            )
        elif action in {"sources", "source-list"}:
            _print(
                service.list_ingest_sources(
                    enabled=True if args.enabled_only else None
                )
            )
        elif action == "source-show":
            _print(service.get_ingest_source(args.source_id))
        elif action == "source-enable":
            _print(
                service.set_ingest_source_enabled(
                    args.source_id,
                    enabled=True,
                )
            )
        elif action == "source-disable":
            _print(
                service.set_ingest_source_enabled(
                    args.source_id,
                    enabled=False,
                )
            )
        elif action == "source-scan":
            _print(
                service.scan_ingest_sources(source_id=args.source_id)
            )
        elif action == "profile-add":
            _print(
                service.add_profile_fact(
                    category=args.category,
                    fact_key=args.fact_key,
                    fact_value=" ".join(args.fact_value),
                    privacy=args.privacy,
                    source_type=args.source_type,
                    source_uri=args.source_uri,
                    confidence=args.confidence,
                    valid_until=args.valid_until,
                )
            )
        elif action in {"profile", "profile-list"}:
            _print(service.list_profile_facts(status=args.status))
        elif action == "profile-show":
            _print(service.get_profile_fact(args.fact_id))
        elif action == "profile-confirm":
            _print(service.confirm_profile_fact(args.fact_id))
        elif action == "profile-reject":
            _print(service.reject_profile_fact(args.fact_id))
        elif action == "profile-revoke":
            _print(service.revoke_profile_fact(args.fact_id))
        elif action == "radar-propose":
            _print(service.generate_monitor_proposals())
        elif action in {"monitors", "monitor-list"}:
            _print(service.list_monitor_proposals(status=args.status))
        elif action == "monitor-show":
            _print(service.get_monitor_proposal(args.proposal_id))
        elif action == "monitor-approve":
            _print(
                service.decide_monitor_proposal(
                    args.proposal_id,
                    decision="approved",
                )
            )
        elif action == "monitor-reject":
            _print(
                service.decide_monitor_proposal(
                    args.proposal_id,
                    decision="rejected",
                )
            )
        elif action == "calendar-preview":
            _print(
                service.calendar_preview(
                    args.item_id,
                    calendar_id=args.calendar,
                    kinds=_parse_kinds(args.kinds),
                )
            )
        elif action == "calendar-sync":
            _print(
                service.calendar_sync(
                    args.item_id,
                    confirmed=bool(args.confirm),
                    calendar_id=args.calendar,
                    kinds=_parse_kinds(args.kinds),
                )
            )
        elif action == "calendar-forget":
            _print(
                service.calendar_forget(
                    args.item_id,
                    confirmed_external_cleanup=bool(args.confirm_external_cleanup),
                    calendar_id=args.calendar,
                    kinds=_parse_kinds(args.kinds),
                )
            )
        elif action == "goal-add":
            _print(
                service.create_goal(
                    title=" ".join(args.title),
                    area=args.area,
                    outcome=args.outcome,
                    target_date=args.target_date,
                    weekly_target_minutes=args.weekly_minutes,
                    priority=args.priority,
                )
            )
        elif action in {"goals", "goal-list"}:
            _print(service.list_goals(status=args.status, limit=args.limit))
        elif action == "goal-status":
            _print(service.set_goal_status(args.goal_id, args.status))
        elif action == "task-add":
            _print(
                service.create_task(
                    title=" ".join(args.title) if args.title else None,
                    goal_id=args.goal,
                    planner_item_id=args.item,
                    notes=args.notes,
                    earliest_start=args.earliest,
                    due_at=args.due,
                    estimated_minutes=args.estimate,
                    min_block_minutes=args.min_block,
                    splittable=not args.not_splittable,
                    energy=args.energy,
                    priority=args.priority,
                )
            )
        elif action in {"tasks", "task-list"}:
            _print(
                service.list_tasks(
                    status=args.status, goal_id=args.goal, limit=args.limit
                )
            )
        elif action == "task-progress":
            _print(
                service.add_task_progress(
                    args.task_id, args.minutes, note=args.note
                )
            )
        elif action == "task-done":
            _print(service.complete_task(args.task_id, note=args.note))
        elif action == "availability-add":
            _print(
                service.add_availability(
                    weekday=args.weekday,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    energy=args.energy,
                    label=args.label,
                )
            )
        elif action == "availability":
            _print(service.list_availability(weekday=args.weekday))
        elif action == "availability-clear":
            _print(
                service.clear_availability(
                    availability_id=args.availability_id,
                    weekday=args.weekday,
                )
            )
        elif action == "milestone-preview":
            _print(
                service.milestone_preview(
                    days=args.days,
                    start_at=args.start,
                    use_calendar=args.calendar_busy,
                )
            )
        elif action == "milestone-overview":
            _print(service.milestone_overview())
        elif action == "milestone-show":
            plan = service.store.get_milestone_plan(args.plan_id)
            if plan is None:
                raise PlannerError(f"milestone plan not found: {args.plan_id}")
            _print(plan)
        elif action == "milestone-confirm":
            _print(
                service.confirm_milestone_plan(
                    args.plan_id,
                    confirmed=bool(args.confirm),
                    replace_plan_id=args.replace,
                )
            )
        elif action == "milestone-reject":
            _print(service.reject_milestone_plan(args.plan_id))
        elif action == "milestone-checkin":
            _print(
                service.milestone_checkin(
                    args.milestone_id,
                    outcome=args.outcome,
                    actual_minutes=args.minutes,
                    completion_fraction=args.fraction,
                    note=args.note,
                )
            )
        elif action == "milestone-snooze":
            _print(
                service.milestone_snooze(
                    args.milestone_id, minutes=args.minutes
                )
            )
        elif action == "schedule-preview":
            _print(
                service.schedule_preview(
                    days=args.days,
                    start_at=args.start,
                    replan=bool(args.replan),
                    use_calendar=args.calendar_busy,
                    calendar_ids=_parse_csv(args.calendar_ids),
                )
            )
        elif action == "schedule-show":
            _print(service.get_schedule(args.plan_id))
        elif action == "schedule-list":
            _print(service.list_schedules(limit=args.limit))
        elif action == "schedule-confirm":
            _print(
                service.confirm_schedule(
                    args.plan_id,
                    confirmed=bool(args.confirm),
                    replace=bool(args.replace),
                )
            )
        elif action == "block-show":
            _print(service.get_block(args.block_id))
        elif action == "block-done":
            _print(
                service.checkin_block(
                    args.block_id,
                    outcome="done",
                    actual_minutes=args.actual_minutes,
                    note=args.note,
                )
            )
        elif action == "block-skip":
            _print(
                service.checkin_block(
                    args.block_id,
                    outcome="skipped",
                    note=args.note,
                    propose_replan=bool(args.propose_replan),
                )
            )
        elif action == "block-defer":
            _print(
                service.checkin_block(
                    args.block_id,
                    outcome="deferred",
                    note=args.note,
                    propose_replan=bool(args.propose_replan),
                )
            )
        elif action == "schedule-calendar-preview":
            _print(
                service.schedule_calendar_preview(
                    args.plan_id, calendar_id=args.calendar
                )
            )
        elif action == "schedule-calendar-sync":
            _print(
                service.schedule_calendar_sync(
                    args.plan_id,
                    confirmed=bool(args.confirm),
                    calendar_id=args.calendar,
                )
            )
        elif action == "schedule-calendar-forget":
            _print(
                service.schedule_calendar_forget(
                    args.plan_id,
                    confirmed_external_cleanup=bool(
                        args.confirm_external_cleanup
                    ),
                    calendar_id=args.calendar,
                )
            )
        elif action == "calendar-busy":
            _print(
                service.calendar_busy(
                    start_at=args.start,
                    end_at=args.end,
                    calendar_ids=_parse_csv(args.calendar_ids),
                )
            )
        elif action == "review":
            _print(
                service.daily_review(
                    review_date=args.date,
                    use_calendar=args.calendar_busy,
                    calendar_ids=_parse_csv(args.calendar_ids),
                    propose_replan=bool(args.propose_replan),
                )
            )
        elif action == "review-setup":
            _print(
                ensure_review_job(
                    schedule=args.schedule, delivery=args.delivery
                )
            )
        elif action == "ingest-setup":
            _print(
                ensure_ingest_job(
                    schedule=args.schedule, delivery=args.delivery
                )
            )
        elif action == "setup":
            _print(ensure_dispatch_job(schedule=args.schedule, delivery=args.delivery))
        else:
            raise PlannerError(f"unknown Planner action: {action}")
        return 0
    except (PlannerError, ValueError, RuntimeError) as exc:
        print(f"personal planner: {exc}")
        return 1


def _parse_kinds(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_csv(value: str | None) -> list[str] | None:
    return _parse_kinds(value)
