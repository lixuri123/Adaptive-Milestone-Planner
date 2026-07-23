"""Public-Cron integration for the Planner dispatch loop."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

from .config import load_settings


DISPATCH_JOB_NAME = "Personal planner dispatch"
DISPATCH_SCRIPT_NAME = "personal_planner_dispatch.py"
REVIEW_JOB_NAME = "Personal planner daily review"
REVIEW_SCRIPT_NAME = "personal_planner_daily_review.py"
INGEST_JOB_NAME = "Personal planner inbox scan"
INGEST_SCRIPT_NAME = "personal_planner_inbox_scan.py"
RADAR_JOB_NAME = "Personal planner information radar"
AGENT_DAILY_JOB_NAME = "Personal planner agent daily plan"
AGENT_WEEKLY_JOB_NAME = "Personal planner agent weekly strategy"

RADAR_PROMPT = """Run the approved Personal Planner information radar.

1. Use the personal_planner tool with action=monitor_list and
   monitor_status=approved. If there are no approved proposals, finish without
   creating anything.
2. For every approved proposal, use its exact query_terms, source_kinds,
   cadence, rationale, and evidence as an allow-list. Search the current web
   for relevant information. Prefer first-party university, department,
   government, conference, journal, employer-career, and examination sources.
   Do not use unsourced summaries as evidence.
3. Open the official source before acting. Capture only a new or materially
   changed notice that is relevant to the approved proposal and that contains
   an actionable opportunity, deadline, registration window, event, policy
   change, or long-lead milestone.
4. Save each result with personal_planner action=capture, source_type=web_radar,
   the canonical source_uri, a concise title, and the source text needed for
   later review. Preserve distinct discovered_at, actionable_at, due_at, and
   event_at values only when the official source states or unambiguously
   implies them. Never invent a date.
5. Never confirm a captured draft, approve another monitor, or write to a
   calendar. Planner deduplication will collapse repeated source URLs and
   unchanged content. Report only newly captured or materially updated drafts
   and any source errors; otherwise respond with NO_UPDATES.
"""

AGENT_DAILY_PROMPT = """Run the event-oriented Personal Planner morning review.

1. Record one daily.review_due event with personal_planner action=event_record,
   source_type=cron, and a dedupe_key containing the current local date.
2. Read personal_planner action=milestone_overview. Use only today's milestone
   targets, remaining P50/P90 workload, pending events, and pending plan approval.
3. If no planning state changed, render a concise deterministic preview of the
   stored targets and remaining workload; do not invent a new strategy.
4. Analyze a pending event only when evidence or feasibility materially changed.
   Create at most one reversible milestone draft and expose its exact review ID.
5. Never confirm or reject a plan, mark work complete, approve a monitor, cancel
   work, or write/delete calendar events. If nothing needs the user, return [SILENT].
"""

AGENT_WEEKLY_PROMPT = """Run the event-oriented Personal Planner weekly review.

1. Read milestone_overview and list at most 20 pending events. Continue only if
   an event is new/changed or the confirmed plan has a material feasibility risk.
2. Route only relevant research, career, or communication experts. Use
   expert_analyze when evidence is already present. Use expert_prepare plus the
   existing Hermes delegate_task only when read-only evidence gathering is needed.
3. Persist the selected event IDs and experts through the Planner analysis action.
   Create at most one milestone draft and clearly expose its assumptions, risks,
   workload, tradeoffs, and diff from the confirmed plan.
4. Never confirm or reject a plan, mark work complete, approve a monitor, cancel
   work, or write an external calendar. Every high-impact mutation needs explicit
   interactive approval.
5. Return [SILENT] when no event or material risk changed. Otherwise report only
   the new draft ID, key feasibility conclusion, and exact user decision required.
"""

_AUTOMATION_JOBS = {
    "dispatch": DISPATCH_JOB_NAME,
    "review": REVIEW_JOB_NAME,
    "ingest": INGEST_JOB_NAME,
    "radar": RADAR_JOB_NAME,
    "agent_daily": AGENT_DAILY_JOB_NAME,
    "agent_weekly": AGENT_WEEKLY_JOB_NAME,
}


def _script_content(plugin_dir: Path) -> str:
    plugin_parent = str(plugin_dir.parent.resolve())
    return (
        "from __future__ import annotations\n"
        "import sys\n"
        f"sys.path.insert(0, {json.dumps(plugin_parent)})\n"
        "import cron\n"
        "from personal_planner.service import PlannerService\n\n"
        "jobs = cron.list_jobs(include_disabled=True)\n"
        f"job = next((row for row in jobs if row.get('name') == "
        f"{json.dumps(DISPATCH_JOB_NAME)}), {{}})\n"
        "delivery_target = str(job.get('deliver') or 'local')\n"
        "message = PlannerService().dispatch_due(\n"
        "    cron_job_state=job, delivery_target=delivery_target\n"
        ")\n"
        "if message:\n"
        "    print(message)\n"
    )


def install_dispatch_script(*, plugin_dir: Path | None = None) -> Path:
    plugin_dir = plugin_dir or Path(__file__).resolve().parent
    scripts_dir = Path(get_hermes_home()) / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    target = scripts_dir / DISPATCH_SCRIPT_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(_script_content(plugin_dir), encoding="utf-8")
    os.replace(temporary, target)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    return target


def _review_script_content(plugin_dir: Path) -> str:
    plugin_parent = str(plugin_dir.parent.resolve())
    return (
        "from __future__ import annotations\n"
        "import sys\n"
        f"sys.path.insert(0, {json.dumps(plugin_parent)})\n"
        "from personal_planner.review import render_daily_review_message\n"
        "from personal_planner.service import PlannerService\n\n"
        "service = PlannerService()\n"
        "report = service.daily_review()\n"
        "overview = service.milestone_overview()\n"
        "today_milestones = [row for row in overview['today_milestones'] "
        "if row.get('status') in {'planned', 'in_progress'}]\n"
        "if today_milestones:\n"
        "    service.record_event(\n"
        "        event_type='checkin.review_due', source='cron',\n"
        "        dedupe_key=f\"checkin.review_due:{report['date']}\",\n"
        "        payload={'date': report['date'], "
        "'milestone_ids': [row['id'] for row in today_milestones]},\n"
        "    )\n"
        "message = render_daily_review_message(report)\n"
        "if today_milestones:\n"
        "    message += '\\n\\nCheck in on: ' + ', '.join(\n"
        "        f\"[{row['id']}] {row['title']}\" for row in today_milestones\n"
        "    )\n"
        "print(message)\n"
    )


def install_review_script(*, plugin_dir: Path | None = None) -> Path:
    plugin_dir = plugin_dir or Path(__file__).resolve().parent
    scripts_dir = Path(get_hermes_home()) / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    target = scripts_dir / REVIEW_SCRIPT_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(_review_script_content(plugin_dir), encoding="utf-8")
    os.replace(temporary, target)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    return target


def _ingest_script_content(plugin_dir: Path) -> str:
    plugin_parent = str(plugin_dir.parent.resolve())
    return (
        "from __future__ import annotations\n"
        "import sys\n"
        f"sys.path.insert(0, {json.dumps(plugin_parent)})\n"
        "from personal_planner.gmail_ingest import render_ingest_message\n"
        "from personal_planner.service import PlannerService\n\n"
        "report = PlannerService().scan_ingest_sources()\n"
        "message = render_ingest_message(report)\n"
        "if message:\n"
        "    print(message)\n"
    )


def install_ingest_script(*, plugin_dir: Path | None = None) -> Path:
    plugin_dir = plugin_dir or Path(__file__).resolve().parent
    scripts_dir = Path(get_hermes_home()) / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    target = scripts_dir / INGEST_SCRIPT_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(_ingest_script_content(plugin_dir), encoding="utf-8")
    os.replace(temporary, target)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    return target


def ensure_dispatch_job(
    *,
    schedule: str | None = None,
    delivery: str | None = None,
    cron_api: Any | None = None,
    plugin_dir: Path | None = None,
) -> dict[str, Any]:
    """Install/update the one recurring Cron job used by the Planner."""
    settings = load_settings()
    schedule = str(schedule or settings.dispatch_schedule).strip()
    delivery = str(delivery or settings.delivery).strip()
    script_path = install_dispatch_script(plugin_dir=plugin_dir)

    if cron_api is None:
        import cron as cron_api  # type: ignore[no-redef]

    jobs = cron_api.list_jobs(include_disabled=True)
    matches = [job for job in jobs if job.get("name") == DISPATCH_JOB_NAME]
    if len(matches) > 1:
        ids = ", ".join(str(job.get("id")) for job in matches)
        raise RuntimeError(
            f"multiple Planner dispatch jobs exist ({ids}); resolve the duplicates "
            "before running setup again"
        )
    updates = {
        "name": DISPATCH_JOB_NAME,
        "schedule": schedule,
        "script": script_path.name,
        "no_agent": True,
        "deliver": delivery,
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
    }
    if matches:
        job = cron_api.update_job(matches[0]["id"], updates)
        if job is None:
            raise RuntimeError("failed to update Planner dispatch Cron job")
        action = "updated"
    else:
        job = cron_api.create_job(
            prompt=None,
            schedule=schedule,
            name=DISPATCH_JOB_NAME,
            deliver=delivery,
            script=script_path.name,
            no_agent=True,
        )
        action = "created"
    return {"action": action, "job": job, "script": str(script_path)}


def ensure_review_job(
    *,
    schedule: str | None = None,
    delivery: str | None = None,
    cron_api: Any | None = None,
    plugin_dir: Path | None = None,
) -> dict[str, Any]:
    """Install/update the optional no-agent daily review delivery job."""
    settings = load_settings()
    schedule = str(schedule or settings.end_of_day_checkin_schedule).strip()
    delivery = str(delivery or settings.delivery).strip()
    script_path = install_review_script(plugin_dir=plugin_dir)

    if cron_api is None:
        import cron as cron_api  # type: ignore[no-redef]

    jobs = cron_api.list_jobs(include_disabled=True)
    matches = [job for job in jobs if job.get("name") == REVIEW_JOB_NAME]
    if len(matches) > 1:
        ids = ", ".join(str(job.get("id")) for job in matches)
        raise RuntimeError(
            f"multiple Planner review jobs exist ({ids}); resolve the duplicates "
            "before running review-setup again"
        )
    updates = {
        "name": REVIEW_JOB_NAME,
        "schedule": schedule,
        "script": script_path.name,
        "no_agent": True,
        "deliver": delivery,
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
    }
    if matches:
        job = cron_api.update_job(matches[0]["id"], updates)
        if job is None:
            raise RuntimeError("failed to update Planner review Cron job")
        action = "updated"
    else:
        job = cron_api.create_job(
            prompt=None,
            schedule=schedule,
            name=REVIEW_JOB_NAME,
            deliver=delivery,
            script=script_path.name,
            no_agent=True,
        )
        action = "created"
    return {"action": action, "job": job, "script": str(script_path)}


def ensure_ingest_job(
    *,
    schedule: str | None = None,
    delivery: str | None = None,
    cron_api: Any | None = None,
    plugin_dir: Path | None = None,
) -> dict[str, Any]:
    """Install/update the optional no-agent Gmail inbox scan job."""
    settings = load_settings()
    schedule = str(schedule or settings.ingest_schedule).strip()
    delivery = str(delivery or settings.delivery).strip()
    script_path = install_ingest_script(plugin_dir=plugin_dir)

    if cron_api is None:
        import cron as cron_api  # type: ignore[no-redef]

    jobs = cron_api.list_jobs(include_disabled=True)
    matches = [job for job in jobs if job.get("name") == INGEST_JOB_NAME]
    if len(matches) > 1:
        ids = ", ".join(str(job.get("id")) for job in matches)
        raise RuntimeError(
            f"multiple Planner inbox scan jobs exist ({ids}); resolve the "
            "duplicates before running ingest-setup again"
        )
    updates = {
        "name": INGEST_JOB_NAME,
        "schedule": schedule,
        "script": script_path.name,
        "no_agent": True,
        "deliver": delivery,
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
    }
    if matches:
        job = cron_api.update_job(matches[0]["id"], updates)
        if job is None:
            raise RuntimeError("failed to update Planner inbox scan Cron job")
        action = "updated"
    else:
        job = cron_api.create_job(
            prompt=None,
            schedule=schedule,
            name=INGEST_JOB_NAME,
            deliver=delivery,
            script=script_path.name,
            no_agent=True,
        )
        action = "created"
    return {"action": action, "job": job, "script": str(script_path)}


def ensure_radar_job(
    *,
    schedule: str | None = None,
    delivery: str | None = None,
    cron_api: Any | None = None,
) -> dict[str, Any]:
    """Install/update the agent-backed approved information-radar scan."""
    settings = load_settings()
    schedule = str(schedule or settings.radar_schedule).strip()
    delivery = str(delivery or settings.delivery).strip()
    if cron_api is None:
        import cron as cron_api  # type: ignore[no-redef]

    jobs = cron_api.list_jobs(include_disabled=True)
    matches = [job for job in jobs if job.get("name") == RADAR_JOB_NAME]
    if len(matches) > 1:
        ids = ", ".join(str(job.get("id")) for job in matches)
        raise RuntimeError(
            f"multiple Planner radar jobs exist ({ids}); resolve the duplicates "
            "before enabling automation again"
        )
    updates = {
        "name": RADAR_JOB_NAME,
        "prompt": RADAR_PROMPT,
        "schedule": schedule,
        "deliver": delivery,
        "script": None,
        "no_agent": False,
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
    }
    if matches:
        job = cron_api.update_job(matches[0]["id"], updates)
        if job is None:
            raise RuntimeError("failed to update Planner information radar Cron job")
        action = "updated"
    else:
        job = cron_api.create_job(
            prompt=RADAR_PROMPT,
            schedule=schedule,
            name=RADAR_JOB_NAME,
            deliver=delivery,
        )
        action = "created"
    return {"action": action, "job": job}


def ensure_agent_daily_job(
    *,
    schedule: str | None = None,
    delivery: str | None = None,
    cron_api: Any | None = None,
) -> dict[str, Any]:
    """Install/update the agent-backed morning planning review."""
    settings = load_settings()
    return _ensure_agent_prompt_job(
        name=AGENT_DAILY_JOB_NAME,
        prompt=AGENT_DAILY_PROMPT,
        schedule=str(schedule or settings.morning_preview_schedule).strip(),
        delivery=str(delivery or settings.delivery).strip(),
        cron_api=cron_api,
    )


def ensure_agent_weekly_job(
    *,
    schedule: str | None = None,
    delivery: str | None = None,
    cron_api: Any | None = None,
) -> dict[str, Any]:
    """Install/update the agent-backed weekly strategy review."""
    settings = load_settings()
    return _ensure_agent_prompt_job(
        name=AGENT_WEEKLY_JOB_NAME,
        prompt=AGENT_WEEKLY_PROMPT,
        schedule=str(schedule or settings.agent_weekly_schedule).strip(),
        delivery=str(delivery or settings.delivery).strip(),
        cron_api=cron_api,
    )


def _ensure_agent_prompt_job(
    *,
    name: str,
    prompt: str,
    schedule: str,
    delivery: str,
    cron_api: Any | None,
) -> dict[str, Any]:
    if cron_api is None:
        import cron as cron_api  # type: ignore[no-redef]

    jobs = cron_api.list_jobs(include_disabled=True)
    matches = [job for job in jobs if job.get("name") == name]
    if len(matches) > 1:
        ids = ", ".join(str(job.get("id")) for job in matches)
        raise RuntimeError(
            f"multiple {name} Cron jobs exist ({ids}); resolve the duplicates "
            "before enabling automation again"
        )
    updates = {
        "name": name,
        "prompt": prompt,
        "schedule": schedule,
        "deliver": delivery,
        "script": None,
        "no_agent": False,
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
    }
    if matches:
        job = cron_api.update_job(matches[0]["id"], updates)
        if job is None:
            raise RuntimeError(f"failed to update {name} Cron job")
        action = "updated"
    else:
        job = cron_api.create_job(
            prompt=prompt,
            schedule=schedule,
            name=name,
            deliver=delivery,
        )
        action = "created"
    return {"action": action, "job": job}


def automation_status(*, cron_api: Any | None = None) -> dict[str, Any]:
    """Return a bounded, Desktop-safe status for Planner-owned Cron jobs."""
    if cron_api is None:
        import cron as cron_api  # type: ignore[no-redef]

    jobs = cron_api.list_jobs(include_disabled=True)
    result: dict[str, Any] = {}
    for key, name in _AUTOMATION_JOBS.items():
        matches = [job for job in jobs if job.get("name") == name]
        if not matches:
            result[key] = {
                "installed": False,
                "enabled": False,
                "state": "missing",
                "name": name,
            }
            continue
        if len(matches) > 1:
            result[key] = {
                "installed": True,
                "enabled": False,
                "state": "error",
                "name": name,
                "error": "duplicate jobs",
                "job_ids": [str(job.get("id")) for job in matches],
            }
            continue
        job = matches[0]
        result[key] = {
            "installed": True,
            "enabled": bool(job.get("enabled", True)),
            "state": str(job.get("state") or "scheduled"),
            "name": name,
            "job_id": str(job.get("id") or ""),
            "schedule": job.get("schedule_display") or job.get("schedule"),
            "next_run_at": job.get("next_run_at"),
            "last_run_at": job.get("last_run_at"),
            "last_status": job.get("last_status"),
            "last_error": job.get("last_error") or job.get("last_delivery_error"),
        }
    enabled_count = sum(1 for value in result.values() if value["enabled"])
    return {
        "enabled": enabled_count > 0,
        "enabled_count": enabled_count,
        "jobs": result,
    }


def ensure_automation_jobs(
    *,
    delivery: str | None = None,
    cron_api: Any | None = None,
) -> dict[str, Any]:
    """Enable the complete Planner automation loop with one explicit action."""
    if cron_api is None:
        import cron as cron_api  # type: ignore[no-redef]

    jobs = {
        "dispatch": ensure_dispatch_job(delivery=delivery, cron_api=cron_api),
        "review": ensure_review_job(delivery=delivery, cron_api=cron_api),
        "ingest": ensure_ingest_job(delivery=delivery, cron_api=cron_api),
        "radar": ensure_radar_job(delivery=delivery, cron_api=cron_api),
        "agent_daily": ensure_agent_daily_job(
            delivery=delivery, cron_api=cron_api
        ),
        "agent_weekly": ensure_agent_weekly_job(
            delivery=delivery, cron_api=cron_api
        ),
    }
    return {"results": jobs, **automation_status(cron_api=cron_api)}


def pause_automation_jobs(*, cron_api: Any | None = None) -> dict[str, Any]:
    """Pause every Planner-owned Cron job without deleting user history."""
    if cron_api is None:
        import cron as cron_api  # type: ignore[no-redef]

    jobs = cron_api.list_jobs(include_disabled=True)
    paused = []
    for job in jobs:
        if job.get("name") not in _AUTOMATION_JOBS.values():
            continue
        result = cron_api.pause_job(
            str(job["id"]), reason="paused from Personal Planner"
        )
        if result is not None:
            paused.append(str(job["id"]))
    return {"paused_job_ids": paused, **automation_status(cron_api=cron_api)}
