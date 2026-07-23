"""Planner configuration loaded from the active Hermes profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_DISPATCH_SCHEDULE = "every 10m"
DEFAULT_DELIVERY = "local"
DEFAULT_REMINDER_OFFSETS_HOURS = (72, 24, 3, 0)
DEFAULT_CALENDAR_ID = "primary"
DEFAULT_CALENDAR_KINDS = ("actionable", "deadline", "event")
DEFAULT_EVENT_DURATION_MINUTES = 60
DEFAULT_PLANNING_HORIZON_DAYS = 14
DEFAULT_PLANNING_BUFFER_PERCENT = 20
DEFAULT_TASK_MINUTES = 60
DEFAULT_MIN_BLOCK_MINUTES = 30
DEFAULT_MAX_BLOCK_MINUTES = 120
DEFAULT_CALENDAR_BUSY_READ = False
DEFAULT_CALENDAR_BUSY_IDS = ("primary",)
DEFAULT_CALENDAR_BUSY_CACHE_MINUTES = 30
DEFAULT_REVIEW_LOOKAHEAD_HOURS = 48
DEFAULT_REPLAN_OVERDUE_MINUTES = 30
DEFAULT_REVIEW_SCHEDULE = "0 21 * * *"
DEFAULT_SCHEDULE_DRAFT_TTL_MINUTES = 30
DEFAULT_INGEST_SCHEDULE = "every 30m"
DEFAULT_RADAR_SCHEDULE = "every 6h"
DEFAULT_AGENT_RUNTIME_ENABLED = True
DEFAULT_AGENT_MODE = "drafting"
DEFAULT_AGENT_CONTEXT_MAX_CHARS = 7000
DEFAULT_AGENT_DAILY_SCHEDULE = "0 8 * * *"
DEFAULT_AGENT_WEEKLY_SCHEDULE = "0 20 * * 0"
DEFAULT_PLANNING_GRANULARITY = "daily_milestone"
DEFAULT_REMINDER_MODE = "recommended_window"
DEFAULT_DAILY_BUFFER_PERCENT = 25
DEFAULT_MAX_DAILY_DOMAINS = 2
DEFAULT_PROTECT_HARD_DEADLINE_DAY = True
DEFAULT_REMINDER_RECOVERY_MINUTES = 30
DEFAULT_REMINDER_MERGE_MINUTES = 60
DEFAULT_ESTIMATE_LEARNING_ENABLED = True
DEFAULT_MORNING_PREVIEW_SCHEDULE = "0 8 * * *"
DEFAULT_END_OF_DAY_CHECKIN_SCHEDULE = "30 21 * * *"


@dataclass(frozen=True)
class PlannerSettings:
    timezone: str = DEFAULT_TIMEZONE
    dispatch_schedule: str = DEFAULT_DISPATCH_SCHEDULE
    delivery: str = DEFAULT_DELIVERY
    reminder_offsets_hours: tuple[int, ...] = DEFAULT_REMINDER_OFFSETS_HOURS
    calendar_id: str = DEFAULT_CALENDAR_ID
    calendar_kinds: tuple[str, ...] = DEFAULT_CALENDAR_KINDS
    event_duration_minutes: int = DEFAULT_EVENT_DURATION_MINUTES
    planning_horizon_days: int = DEFAULT_PLANNING_HORIZON_DAYS
    planning_buffer_percent: int = DEFAULT_PLANNING_BUFFER_PERCENT
    default_task_minutes: int = DEFAULT_TASK_MINUTES
    default_min_block_minutes: int = DEFAULT_MIN_BLOCK_MINUTES
    max_block_minutes: int = DEFAULT_MAX_BLOCK_MINUTES
    calendar_busy_read: bool = DEFAULT_CALENDAR_BUSY_READ
    calendar_busy_ids: tuple[str, ...] = DEFAULT_CALENDAR_BUSY_IDS
    calendar_busy_cache_minutes: int = DEFAULT_CALENDAR_BUSY_CACHE_MINUTES
    review_lookahead_hours: int = DEFAULT_REVIEW_LOOKAHEAD_HOURS
    replan_overdue_minutes: int = DEFAULT_REPLAN_OVERDUE_MINUTES
    review_schedule: str = DEFAULT_REVIEW_SCHEDULE
    schedule_draft_ttl_minutes: int = DEFAULT_SCHEDULE_DRAFT_TTL_MINUTES
    ingest_schedule: str = DEFAULT_INGEST_SCHEDULE
    radar_schedule: str = DEFAULT_RADAR_SCHEDULE
    agent_runtime_enabled: bool = DEFAULT_AGENT_RUNTIME_ENABLED
    agent_mode: str = DEFAULT_AGENT_MODE
    agent_context_max_chars: int = DEFAULT_AGENT_CONTEXT_MAX_CHARS
    agent_daily_schedule: str = DEFAULT_AGENT_DAILY_SCHEDULE
    agent_weekly_schedule: str = DEFAULT_AGENT_WEEKLY_SCHEDULE
    planning_granularity: str = DEFAULT_PLANNING_GRANULARITY
    reminder_mode: str = DEFAULT_REMINDER_MODE
    daily_buffer_percent: int = DEFAULT_DAILY_BUFFER_PERCENT
    max_daily_domains: int = DEFAULT_MAX_DAILY_DOMAINS
    protect_hard_deadline_day: bool = DEFAULT_PROTECT_HARD_DEADLINE_DAY
    reminder_recovery_minutes: int = DEFAULT_REMINDER_RECOVERY_MINUTES
    reminder_merge_minutes: int = DEFAULT_REMINDER_MERGE_MINUTES
    estimate_learning_enabled: bool = DEFAULT_ESTIMATE_LEARNING_ENABLED
    morning_preview_schedule: str = DEFAULT_MORNING_PREVIEW_SCHEDULE
    end_of_day_checkin_schedule: str = DEFAULT_END_OF_DAY_CHECKIN_SCHEDULE


def _entry_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import cfg_get, read_raw_config

        cfg = read_raw_config()
        entry = cfg_get(cfg, "plugins", "entries", "personal_planner", default={})
        return entry if isinstance(entry, dict) else {}
    except Exception:
        return {}


def load_settings() -> PlannerSettings:
    cfg = _entry_config()

    timezone = str(cfg.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    dispatch_schedule = (
        str(cfg.get("dispatch_schedule") or DEFAULT_DISPATCH_SCHEDULE).strip()
        or DEFAULT_DISPATCH_SCHEDULE
    )
    delivery = str(cfg.get("delivery") or DEFAULT_DELIVERY).strip() or DEFAULT_DELIVERY

    raw_offsets = cfg.get("reminder_offsets_hours", DEFAULT_REMINDER_OFFSETS_HOURS)
    offsets: list[int] = []
    if isinstance(raw_offsets, (list, tuple)):
        for value in raw_offsets:
            try:
                hours = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= hours <= 24 * 365:
                offsets.append(hours)
    if not offsets:
        offsets = list(DEFAULT_REMINDER_OFFSETS_HOURS)

    calendar_id = str(cfg.get("calendar_id") or DEFAULT_CALENDAR_ID).strip()
    raw_kinds = cfg.get("calendar_kinds", DEFAULT_CALENDAR_KINDS)
    calendar_kinds: list[str] = []
    if isinstance(raw_kinds, (list, tuple)):
        calendar_kinds = [
            str(value).strip().lower()
            for value in raw_kinds
            if str(value).strip().lower() in DEFAULT_CALENDAR_KINDS
        ]
    if not calendar_kinds:
        calendar_kinds = list(DEFAULT_CALENDAR_KINDS)
    try:
        event_duration_minutes = int(
            cfg.get("event_duration_minutes", DEFAULT_EVENT_DURATION_MINUTES)
        )
    except (TypeError, ValueError):
        event_duration_minutes = DEFAULT_EVENT_DURATION_MINUTES
    event_duration_minutes = max(5, min(event_duration_minutes, 24 * 60))
    planning_horizon_days = _bounded_int(
        cfg.get("planning_horizon_days"),
        default=DEFAULT_PLANNING_HORIZON_DAYS,
        minimum=1,
        maximum=42,
    )
    planning_buffer_percent = _bounded_int(
        cfg.get("planning_buffer_percent"),
        default=DEFAULT_PLANNING_BUFFER_PERCENT,
        minimum=0,
        maximum=60,
    )
    default_task_minutes = _bounded_int(
        cfg.get("default_task_minutes"),
        default=DEFAULT_TASK_MINUTES,
        minimum=5,
        maximum=24 * 60,
    )
    default_min_block_minutes = _bounded_int(
        cfg.get("default_min_block_minutes"),
        default=DEFAULT_MIN_BLOCK_MINUTES,
        minimum=5,
        maximum=8 * 60,
    )
    max_block_minutes = _bounded_int(
        cfg.get("max_block_minutes"),
        default=DEFAULT_MAX_BLOCK_MINUTES,
        minimum=default_min_block_minutes,
        maximum=12 * 60,
    )
    calendar_busy_read = _as_bool(
        cfg.get("calendar_busy_read"), default=DEFAULT_CALENDAR_BUSY_READ
    )
    raw_busy_ids = cfg.get("calendar_busy_ids", DEFAULT_CALENDAR_BUSY_IDS)
    if isinstance(raw_busy_ids, str):
        raw_busy_ids = [raw_busy_ids]
    calendar_busy_ids = (
        tuple(
            dict.fromkeys(
                str(value).strip()
                for value in raw_busy_ids
                if str(value).strip()
            )
        )
        if isinstance(raw_busy_ids, (list, tuple))
        else ()
    )
    if not calendar_busy_ids:
        calendar_busy_ids = (calendar_id or DEFAULT_CALENDAR_ID,)
    calendar_busy_cache_minutes = _bounded_int(
        cfg.get("calendar_busy_cache_minutes"),
        default=DEFAULT_CALENDAR_BUSY_CACHE_MINUTES,
        minimum=1,
        maximum=24 * 60,
    )
    review_lookahead_hours = _bounded_int(
        cfg.get("review_lookahead_hours"),
        default=DEFAULT_REVIEW_LOOKAHEAD_HOURS,
        minimum=1,
        maximum=14 * 24,
    )
    replan_overdue_minutes = _bounded_int(
        cfg.get("replan_overdue_minutes"),
        default=DEFAULT_REPLAN_OVERDUE_MINUTES,
        minimum=0,
        maximum=24 * 60,
    )
    review_schedule = (
        str(cfg.get("review_schedule") or DEFAULT_REVIEW_SCHEDULE).strip()
        or DEFAULT_REVIEW_SCHEDULE
    )
    schedule_draft_ttl_minutes = _bounded_int(
        cfg.get("schedule_draft_ttl_minutes"),
        default=DEFAULT_SCHEDULE_DRAFT_TTL_MINUTES,
        minimum=1,
        maximum=24 * 60,
    )
    ingest_schedule = (
        str(cfg.get("ingest_schedule") or DEFAULT_INGEST_SCHEDULE).strip()
        or DEFAULT_INGEST_SCHEDULE
    )
    radar_schedule = (
        str(cfg.get("radar_schedule") or DEFAULT_RADAR_SCHEDULE).strip()
        or DEFAULT_RADAR_SCHEDULE
    )
    agent_runtime_enabled = _as_bool(
        cfg.get("agent_runtime_enabled"),
        default=DEFAULT_AGENT_RUNTIME_ENABLED,
    )
    agent_mode = str(cfg.get("agent_mode") or DEFAULT_AGENT_MODE).strip().lower()
    if agent_mode not in {"advisory", "drafting"}:
        agent_mode = DEFAULT_AGENT_MODE
    agent_context_max_chars = _bounded_int(
        cfg.get("agent_context_max_chars"),
        default=DEFAULT_AGENT_CONTEXT_MAX_CHARS,
        minimum=2000,
        maximum=16000,
    )
    agent_daily_schedule = (
        str(cfg.get("agent_daily_schedule") or DEFAULT_AGENT_DAILY_SCHEDULE).strip()
        or DEFAULT_AGENT_DAILY_SCHEDULE
    )
    agent_weekly_schedule = (
        str(cfg.get("agent_weekly_schedule") or DEFAULT_AGENT_WEEKLY_SCHEDULE).strip()
        or DEFAULT_AGENT_WEEKLY_SCHEDULE
    )
    planning_granularity = str(
        cfg.get("planning_granularity") or DEFAULT_PLANNING_GRANULARITY
    ).strip().lower()
    if planning_granularity != DEFAULT_PLANNING_GRANULARITY:
        planning_granularity = DEFAULT_PLANNING_GRANULARITY
    reminder_mode = str(
        cfg.get("reminder_mode") or DEFAULT_REMINDER_MODE
    ).strip().lower()
    if reminder_mode != DEFAULT_REMINDER_MODE:
        reminder_mode = DEFAULT_REMINDER_MODE
    daily_buffer_percent = _bounded_int(
        cfg.get("daily_buffer_percent"),
        default=DEFAULT_DAILY_BUFFER_PERCENT,
        minimum=0,
        maximum=80,
    )
    max_daily_domains = _bounded_int(
        cfg.get("max_daily_domains"),
        default=DEFAULT_MAX_DAILY_DOMAINS,
        minimum=1,
        maximum=3,
    )
    protect_hard_deadline_day = _as_bool(
        cfg.get("protect_hard_deadline_day"),
        default=DEFAULT_PROTECT_HARD_DEADLINE_DAY,
    )
    reminder_recovery_minutes = _bounded_int(
        cfg.get("reminder_recovery_minutes"),
        default=DEFAULT_REMINDER_RECOVERY_MINUTES,
        minimum=0,
        maximum=240,
    )
    reminder_merge_minutes = _bounded_int(
        cfg.get("reminder_merge_minutes"),
        default=DEFAULT_REMINDER_MERGE_MINUTES,
        minimum=0,
        maximum=240,
    )
    estimate_learning_enabled = _as_bool(
        cfg.get("estimate_learning_enabled"),
        default=DEFAULT_ESTIMATE_LEARNING_ENABLED,
    )
    morning_preview_schedule = (
        str(
            cfg.get("morning_preview_schedule")
            or cfg.get("agent_daily_schedule")
            or DEFAULT_MORNING_PREVIEW_SCHEDULE
        ).strip()
        or DEFAULT_MORNING_PREVIEW_SCHEDULE
    )
    end_of_day_checkin_schedule = (
        str(
            cfg.get("end_of_day_checkin_schedule")
            or cfg.get("review_schedule")
            or DEFAULT_END_OF_DAY_CHECKIN_SCHEDULE
        ).strip()
        or DEFAULT_END_OF_DAY_CHECKIN_SCHEDULE
    )

    return PlannerSettings(
        timezone=timezone,
        dispatch_schedule=dispatch_schedule,
        delivery=delivery,
        reminder_offsets_hours=tuple(sorted(set(offsets), reverse=True)),
        calendar_id=calendar_id or DEFAULT_CALENDAR_ID,
        calendar_kinds=tuple(dict.fromkeys(calendar_kinds)),
        event_duration_minutes=event_duration_minutes,
        planning_horizon_days=planning_horizon_days,
        planning_buffer_percent=planning_buffer_percent,
        default_task_minutes=default_task_minutes,
        default_min_block_minutes=default_min_block_minutes,
        max_block_minutes=max_block_minutes,
        calendar_busy_read=calendar_busy_read,
        calendar_busy_ids=calendar_busy_ids,
        calendar_busy_cache_minutes=calendar_busy_cache_minutes,
        review_lookahead_hours=review_lookahead_hours,
        replan_overdue_minutes=replan_overdue_minutes,
        review_schedule=review_schedule,
        schedule_draft_ttl_minutes=schedule_draft_ttl_minutes,
        ingest_schedule=ingest_schedule,
        radar_schedule=radar_schedule,
        agent_runtime_enabled=agent_runtime_enabled,
        agent_mode=agent_mode,
        agent_context_max_chars=agent_context_max_chars,
        agent_daily_schedule=agent_daily_schedule,
        agent_weekly_schedule=agent_weekly_schedule,
        planning_granularity=planning_granularity,
        reminder_mode=reminder_mode,
        daily_buffer_percent=daily_buffer_percent,
        max_daily_domains=max_daily_domains,
        protect_hard_deadline_day=protect_hard_deadline_day,
        reminder_recovery_minutes=reminder_recovery_minutes,
        reminder_merge_minutes=reminder_merge_minutes,
        estimate_learning_enabled=estimate_learning_enabled,
        morning_preview_schedule=morning_preview_schedule,
        end_of_day_checkin_schedule=end_of_day_checkin_schedule,
    )


def _bounded_int(
    value: Any, *, default: int, minimum: int, maximum: int
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
