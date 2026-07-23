"""Explicit Google Calendar synchronization for confirmed Planner items."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class CalendarSyncError(RuntimeError):
    pass


def google_workspace_script_path() -> Path:
    import hermes_constants

    repo_root = Path(hermes_constants.__file__).resolve().parent
    return (
        repo_root
        / "skills"
        / "productivity"
        / "google-workspace"
        / "scripts"
        / "google_api.py"
    )


class GoogleWorkspaceCalendar:
    """Thin adapter over Hermes' existing Google Workspace calendar script."""

    provider = "google_workspace"

    def __init__(self, script_path: Path | str | None = None):
        self.script_path = (
            Path(script_path) if script_path is not None else google_workspace_script_path()
        )

    def create_event(
        self,
        *,
        calendar_id: str,
        summary: str,
        start: str,
        end: str,
        description: str,
        location: str = "",
    ) -> dict[str, Any]:
        if not self.script_path.is_file():
            raise CalendarSyncError(
                f"Google Workspace calendar helper not found: {self.script_path}"
            )
        command = [
            sys.executable,
            str(self.script_path),
            "calendar",
            "create",
            "--summary",
            summary,
            "--start",
            start,
            "--end",
            end,
            "--description",
            description,
            "--calendar",
            calendar_id,
        ]
        if location:
            command.extend(["--location", location])
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise CalendarSyncError(
                detail or f"calendar helper exited with code {completed.returncode}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CalendarSyncError(
                f"calendar helper returned invalid JSON: {completed.stdout[:300]}"
            ) from exc
        event_id = str(payload.get("id") or "").strip()
        if not event_id:
            raise CalendarSyncError("calendar helper did not return an event id")
        return payload

    def list_events(
        self,
        *,
        calendar_id: str,
        start: str,
        end: str,
        max_results: int = 2500,
    ) -> list[dict[str, Any]]:
        """Read calendar events through Hermes' existing authenticated helper."""
        if not self.script_path.is_file():
            raise CalendarSyncError(
                f"Google Workspace calendar helper not found: {self.script_path}"
            )
        command = [
            sys.executable,
            str(self.script_path),
            "calendar",
            "list",
            "--start",
            start,
            "--end",
            end,
            "--calendar",
            calendar_id,
            "--max",
            str(max(1, min(int(max_results), 2500))),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise CalendarSyncError(
                detail or f"calendar helper exited with code {completed.returncode}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CalendarSyncError(
                f"calendar helper returned invalid JSON: {completed.stdout[:300]}"
            ) from exc
        if not isinstance(payload, list):
            raise CalendarSyncError("calendar helper did not return an event list")
        return [event for event in payload if isinstance(event, dict)]


def normalize_busy_events(
    events: list[dict[str, Any]],
    *,
    calendar_id: str,
    timezone_name: str,
    parse_datetime_fn,
    utc_iso_fn,
) -> list[dict[str, Any]]:
    """Normalize timed and all-day events into deterministic busy intervals."""
    normalized: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("status") or "").lower() == "cancelled":
            continue
        raw_start = str(event.get("start") or "").strip()
        raw_end = str(event.get("end") or "").strip()
        if not raw_start or not raw_end:
            continue
        try:
            start = parse_datetime_fn(raw_start, timezone_name)
            end = parse_datetime_fn(raw_end, timezone_name)
        except ValueError:
            continue
        if start is None or end is None or end <= start:
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            event_id = _synthetic_event_id(
                calendar_id=calendar_id,
                summary=str(event.get("summary") or ""),
                start=start,
                end=end,
            )
        normalized.append(
            {
                "provider": "google_workspace",
                "calendar_id": calendar_id,
                "event_id": event_id,
                "summary": str(event.get("summary") or "(no title)")[:500],
                "starts_at": utc_iso_fn(start),
                "ends_at": utc_iso_fn(end),
                "all_day": "T" not in raw_start,
                "status": str(event.get("status") or "confirmed"),
                "html_link": str(event.get("htmlLink") or "") or None,
                "raw": event,
            }
        )
    return sorted(normalized, key=lambda event: (event["starts_at"], event["event_id"]))


def event_payloads(
    item: dict[str, Any],
    *,
    kinds: tuple[str, ...],
    local_iso_fn,
) -> list[dict[str, Any]]:
    """Build deterministic event proposals without performing external writes."""
    proposals: list[dict[str, Any]] = []
    specs = {
        "actionable": (
            item.get("actionable_at"),
            f"Action opens: {item['title']}",
            15,
        ),
        "deadline": (
            item.get("due_at"),
            f"Deadline: {item['title']}",
            15,
        ),
        "event": (
            item.get("event_at"),
            item["title"],
            max(5, int(item.get("event_duration_minutes") or 60)),
        ),
    }
    description_lines = [
        f"Hermes Planner item: {item['id']}",
        f"Importance: {item['importance']}",
    ]
    if item.get("source_uri"):
        description_lines.append(f"Source: {item['source_uri']}")
    description_lines.extend(("", str(item.get("raw_content") or "")[:1500]))
    description = "\n".join(description_lines).strip()

    for kind in kinds:
        timestamp, summary, duration_minutes = specs[kind]
        if not timestamp:
            continue
        start = local_iso_fn(timestamp, item["timezone"])
        end = local_iso_fn(
            _add_minutes(timestamp, duration_minutes), item["timezone"]
        )
        proposals.append(
            {
                "kind": kind,
                "summary": summary,
                "start": start,
                "end": end,
                "description": description,
                "location": "",
            }
        )
    return proposals


def plan_block_event_payload(
    block: dict[str, Any],
    *,
    timezone_name: str,
    local_iso_fn,
) -> dict[str, Any]:
    """Build one deterministic focus-block event without writing externally."""
    description_lines = [
        f"Hermes Planner schedule: {block['plan_id']}",
        f"Planner block: {block['id']}",
        f"Planner task: {block['task_id']}",
        f"Planned minutes: {block['minutes']}",
        f"Energy: {block['energy']}",
        "",
        f"Complete: hermes plan block-done {block['id']}",
        f"Skip: hermes plan block-skip {block['id']}",
        f"Defer: hermes plan block-defer {block['id']}",
    ]
    return {
        "block_id": block["id"],
        "task_id": block["task_id"],
        "summary": f"Focus: {block['title']}",
        "start": local_iso_fn(block["starts_at"], timezone_name),
        "end": local_iso_fn(block["ends_at"], timezone_name),
        "description": "\n".join(description_lines),
        "location": "",
    }


def _add_minutes(value: str, minutes: int) -> str:
    from .timeutil import parse_datetime, utc_iso

    parsed = parse_datetime(value, "UTC")
    assert parsed is not None
    return utc_iso(parsed + timedelta(minutes=minutes))


def _synthetic_event_id(
    *, calendar_id: str, summary: str, start: datetime, end: datetime
) -> str:
    import hashlib

    value = "|".join((calendar_id, summary, start.isoformat(), end.isoformat()))
    return "synthetic-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
