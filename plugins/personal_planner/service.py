"""Domain service for Personal Planner."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from .capacity import build_daily_capacity
from .config import PlannerSettings, load_settings
from .calendar_sync import (
    GoogleWorkspaceCalendar,
    event_payloads,
    normalize_busy_events,
    plan_block_event_payload,
)
from .fingerprint import (
    content_hash,
    normalize_content,
    normalize_source_uri,
    source_key,
)
from .contracts import VALID_DOMAINS, validate_work_package
from .expert_agents import (
    build_delegated_expert_goal,
    explain_domain_routing,
    validate_delegated_expert_result,
)
from .gmail_ingest import (
    GoogleWorkspaceGmail,
    gmail_message_content,
    gmail_message_datetime,
    gmail_message_source_uri,
)
from .planning import build_rolling_plan
from .milestones import (
    build_milestone_plan,
    calibrate_agent_work_package,
    diff_milestone_plans,
)
from .milestone_reminders import (
    due_milestone_reminders,
    render_milestone_reminder,
)
from .profile_radar import build_monitor_proposals
from .review import build_daily_review
from .store import PlannerStore
from .timeutil import (
    local_display,
    local_iso,
    parse_datetime,
    resolve_timezone,
    utc_iso,
    utc_now,
)


VALID_IMPORTANCE = {"low", "normal", "high", "critical"}
VALID_CALENDAR_KINDS = {"actionable", "deadline", "event"}
VALID_ENERGY = {"low", "medium", "high"}
VALID_GOAL_STATUS = {"active", "paused", "done", "cancelled"}
VALID_TASK_STATUS = {"backlog", "planned", "done", "cancelled"}
VALID_PROFILE_STATUS = {
    "proposed",
    "confirmed",
    "rejected",
    "revoked",
    "superseded",
}
VALID_PROFILE_PRIVACY = {"standard", "sensitive"}
VALID_MONITOR_STATUS = {
    "proposed",
    "approved",
    "rejected",
    "superseded",
}
WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


class PlannerError(ValueError):
    pass


class PlannerService:
    def __init__(
        self,
        *,
        store: PlannerStore | None = None,
        settings: PlannerSettings | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.store = store or PlannerStore()
        self.settings = settings or load_settings()
        self._now_fn = now_fn or utc_now

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def capture(
        self,
        *,
        raw_content: str,
        title: str | None = None,
        actionable_at: str | datetime | None = None,
        due_at: str | datetime | None = None,
        event_at: str | datetime | None = None,
        discovered_at: str | datetime | None = None,
        timezone_name: str | None = None,
        importance: str | None = None,
        source_type: str = "manual",
        source_uri: str | None = None,
        event_duration_minutes: int | None = None,
    ) -> dict[str, Any]:
        content = str(raw_content or "").strip()
        if not content:
            raise PlannerError("raw_content is required")

        tz_name = str(timezone_name or self.settings.timezone).strip()
        normalized_importance = str(importance or "normal").strip().lower()
        if normalized_importance not in VALID_IMPORTANCE:
            raise PlannerError(
                f"importance must be one of {', '.join(sorted(VALID_IMPORTANCE))}"
            )
        if event_duration_minutes is None:
            duration = self.settings.event_duration_minutes
        else:
            try:
                duration = int(event_duration_minutes)
            except (TypeError, ValueError) as exc:
                raise PlannerError("event_duration_minutes must be an integer") from exc
            if not 5 <= duration <= 24 * 60:
                raise PlannerError("event_duration_minutes must be between 5 and 1440")

        now = self._now()
        discovered = parse_datetime(discovered_at, tz_name) or now
        actionable = parse_datetime(actionable_at, tz_name)
        due = parse_datetime(due_at, tz_name)
        event = parse_datetime(event_at, tz_name)
        if actionable and due and actionable > due:
            raise PlannerError("actionable_at cannot be later than due_at")

        derived_title = str(title or content.splitlines()[0]).strip()[:160]
        if not derived_title:
            derived_title = "Untitled commitment"
        now_iso = utc_iso(now)
        normalized_source_type = (
            str(source_type or "manual").strip().lower() or "manual"
        )
        normalized_uri = normalize_source_uri(source_uri)
        provided_fields = {
            field
            for field, supplied in (
                ("title", title is not None),
                ("actionable_at", actionable_at is not None),
                ("due_at", due_at is not None),
                ("event_at", event_at is not None),
                ("timezone", timezone_name is not None),
                ("importance", importance is not None),
                ("event_duration_minutes", event_duration_minutes is not None),
            )
            if supplied
        }
        captured = self.store.capture_item(
            {
                "title": derived_title,
                "raw_content": content,
                "source_type": normalized_source_type,
                "source_uri": normalized_uri,
                "source_key": source_key(
                    source_type=normalized_source_type,
                    source_uri=normalized_uri,
                    raw_content=content,
                ),
                "content_hash": content_hash(content),
                "discovered_at": utc_iso(discovered),
                "actionable_at": utc_iso(actionable) if actionable else None,
                "due_at": utc_iso(due) if due else None,
                "event_at": utc_iso(event) if event else None,
                "timezone": tz_name,
                "importance": normalized_importance,
                "status": "draft",
                "acknowledged_at": None,
                "completed_at": None,
                "snoozed_until": None,
                "event_duration_minutes": duration,
                "created_at": now_iso,
                "updated_at": now_iso,
                "_provided_fields": provided_fields,
            }
        )
        if captured.get("capture_outcome") in {"created", "updated"}:
            revision_number = len(self.store.list_revisions(captured["id"])) + 1
            event_type = (
                "radar.finding_changed"
                if normalized_source_type == "web_radar"
                else "notice.changed"
            )
            dedupe = f"{captured['id']}:{revision_number}"
            if event_type == "radar.finding_changed":
                dedupe += f":{captured['content_hash']}"
            self.record_event(
                event_type=event_type,
                source=normalized_source_type,
                dedupe_key=dedupe,
                occurred_at=discovered,
                payload={
                    "item_id": captured["id"],
                    "revision_number": revision_number,
                    "title": captured["title"],
                    "source_uri": captured.get("source_uri"),
                    "published_at": captured.get("discovered_at"),
                    "actionable_at": captured.get("actionable_at"),
                    "due_at": captured.get("due_at"),
                    "event_at": captured.get("event_at"),
                    "content_hash": captured.get("content_hash"),
                },
            )
        return captured

    def get(self, item_id: str) -> dict[str, Any]:
        item = self.store.get_item(item_id)
        if item is None:
            raise PlannerError(f"planner item not found: {item_id}")
        return item

    def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status and status not in {"draft", "confirmed", "done", "cancelled"}:
            raise PlannerError("invalid item status")
        return self.store.list_items(status=status, limit=limit)

    def revisions(self, item_id: str) -> list[dict[str, Any]]:
        self.get(item_id)
        return self.store.list_revisions(item_id)

    def add_gmail_source(
        self,
        *,
        name: str,
        query: str,
        importance: str = "normal",
        max_results: int = 25,
    ) -> dict[str, Any]:
        normalized_name = str(name or "").strip()
        normalized_query = str(query or "").strip()
        normalized_importance = _normalize_choice(
            importance,
            VALID_IMPORTANCE,
            field="importance",
        )
        if not normalized_name:
            raise PlannerError("source name is required")
        if len(normalized_name) > 120:
            raise PlannerError("source name must be at most 120 characters")
        if not normalized_query:
            raise PlannerError("Gmail query is required")
        if len(normalized_query) > 1000:
            raise PlannerError("Gmail query must be at most 1000 characters")
        try:
            bounded_max = int(max_results)
        except (TypeError, ValueError) as exc:
            raise PlannerError("max_results must be an integer") from exc
        if not 1 <= bounded_max <= 100:
            raise PlannerError("max_results must be between 1 and 100")
        try:
            return self.store.create_ingest_source(
                provider="gmail",
                name=normalized_name,
                query=normalized_query,
                importance=normalized_importance,
                max_results=bounded_max,
                now=utc_iso(self._now()),
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc

    def get_ingest_source(self, source_id: str) -> dict[str, Any]:
        source = self.store.get_ingest_source(source_id)
        if source is None:
            raise PlannerError(f"ingest source not found: {source_id}")
        source["receipts"] = self.store.list_ingest_receipts(
            source_id=source["id"],
            limit=50,
        )
        return source

    def list_ingest_sources(
        self,
        *,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        return self.store.list_ingest_sources(enabled=enabled)

    def set_ingest_source_enabled(
        self,
        source_id: str,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        source = self.store.set_ingest_source_enabled(
            source_id=source_id,
            enabled=enabled,
            now=utc_iso(self._now()),
        )
        if source is None:
            raise PlannerError(f"ingest source not found: {source_id}")
        return source

    def scan_ingest_sources(
        self,
        *,
        source_id: str | None = None,
        client: Any | None = None,
    ) -> dict[str, Any]:
        if source_id:
            sources = [self.get_ingest_source(source_id)]
        else:
            sources = self.list_ingest_sources(enabled=True)
        gmail = client or GoogleWorkspaceGmail()
        generated_at = utc_iso(self._now())
        report: dict[str, Any] = {
            "generated_at": generated_at,
            "requires_review": True,
            "auto_confirmed": False,
            "sources": [],
            "captured": [],
            "errors": [],
        }
        for source in sources:
            source_report = {
                "source_id": source["id"],
                "name": source["name"],
                "provider": source["provider"],
                "matched": 0,
                "already_processed": 0,
                "captured": 0,
                "errors": [],
            }
            try:
                messages = gmail.search_messages(
                    query=source["query"],
                    max_results=int(source["max_results"]),
                )
            except Exception as exc:
                error = f"{source['name']}: Gmail search failed: {str(exc)[:500]}"
                source_report["errors"].append(error)
                report["errors"].append(error)
                self.store.mark_ingest_source_scan(
                    source_id=source["id"],
                    scanned_at=generated_at,
                    error=error,
                )
                report["sources"].append(source_report)
                continue

            ordered_messages = sorted(
                messages,
                key=lambda value: gmail_message_datetime(
                    value,
                    fallback=self._now(),
                ),
            )
            source_report["matched"] = len(ordered_messages)
            for metadata in ordered_messages:
                message_id = str(metadata.get("id") or "").strip()
                if not message_id:
                    error = f"{source['name']}: Gmail result had no message id"
                    source_report["errors"].append(error)
                    report["errors"].append(error)
                    continue
                if self.store.has_ingest_receipt(
                    source_id=source["id"],
                    provider_item_id=message_id,
                ):
                    source_report["already_processed"] += 1
                    continue
                try:
                    full_message = gmail.get_message(message_id=message_id)
                    message = {**metadata, **full_message}
                    received = gmail_message_datetime(
                        message,
                        fallback=self._now(),
                    )
                    content = gmail_message_content(message)
                    item = self.capture(
                        raw_content=content,
                        title=str(
                            message.get("subject") or "(no subject)"
                        ).strip(),
                        discovered_at=received,
                        importance=source["importance"],
                        source_type="gmail",
                        source_uri=gmail_message_source_uri(message),
                    )
                    inserted = self.store.record_ingest_receipt(
                        source_id=source["id"],
                        provider_item_id=message_id,
                        provider_thread_id=str(
                            message.get("threadId") or ""
                        ).strip()
                        or None,
                        item_id=item["id"],
                        capture_outcome=item["capture_outcome"],
                        subject=str(message.get("subject") or "").strip()
                        or None,
                        sender=str(message.get("from") or "").strip()
                        or None,
                        received_at=utc_iso(received),
                        scanned_at=generated_at,
                    )
                    if not inserted:
                        source_report["already_processed"] += 1
                        continue
                    source_report["captured"] += 1
                    report["captured"].append(
                        {
                            "source_id": source["id"],
                            "source_name": source["name"],
                            "message_id": message_id,
                            "thread_id": str(
                                message.get("threadId") or ""
                            ).strip()
                            or None,
                            "item_id": item["id"],
                            "title": item["title"],
                            "item_status": item["status"],
                            "capture_outcome": item["capture_outcome"],
                            "received_at": utc_iso(received),
                        }
                    )
                except Exception as exc:
                    error = (
                        f"{source['name']}: message {message_id} failed: "
                        f"{str(exc)[:500]}"
                    )
                    source_report["errors"].append(error)
                    report["errors"].append(error)
            source_error = (
                f"{len(source_report['errors'])} message error(s)"
                if source_report["errors"]
                else None
            )
            self.store.mark_ingest_source_scan(
                source_id=source["id"],
                scanned_at=generated_at,
                error=source_error,
            )
            report["sources"].append(source_report)
        report["new_draft_count"] = sum(
            1
            for value in report["captured"]
            if value["capture_outcome"] in {"created", "updated"}
            and value["item_status"] == "draft"
        )
        report["review_item_ids"] = list(
            dict.fromkeys(
                value["item_id"]
                for value in report["captured"]
                if value["item_status"] == "draft"
            )
        )
        return report

    def add_profile_fact(
        self,
        *,
        category: str,
        fact_key: str,
        fact_value: str,
        privacy: str = "standard",
        source_type: str = "user",
        source_uri: str | None = None,
        confidence: float = 1.0,
        valid_until: str | datetime | None = None,
    ) -> dict[str, Any]:
        normalized_category = _profile_identifier(
            category,
            field="profile category",
        )
        normalized_key = _profile_identifier(
            fact_key,
            field="profile key",
        )
        value = str(fact_value or "").strip()
        if not value:
            raise PlannerError("profile value is required")
        if len(value) > 1000:
            raise PlannerError("profile value must be at most 1000 characters")
        normalized_privacy = _normalize_choice(
            privacy,
            VALID_PROFILE_PRIVACY,
            field="privacy",
        )
        try:
            normalized_confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise PlannerError("confidence must be a number") from exc
        if not 0.0 <= normalized_confidence <= 1.0:
            raise PlannerError("confidence must be between 0 and 1")
        parsed_valid_until = parse_datetime(
            valid_until,
            self.settings.timezone,
        )
        return self.store.create_profile_fact(
            category=normalized_category,
            fact_key=normalized_key,
            fact_value=value,
            normalized_value=normalize_content(value),
            privacy=normalized_privacy,
            source_type=str(source_type or "user").strip().lower()[:80]
            or "user",
            source_uri=normalize_source_uri(source_uri),
            confidence=normalized_confidence,
            valid_until=(
                utc_iso(parsed_valid_until) if parsed_valid_until else None
            ),
            now=utc_iso(self._now()),
        )

    def get_profile_fact(self, fact_id: str) -> dict[str, Any]:
        fact = self.store.get_profile_fact(fact_id)
        if fact is None:
            raise PlannerError(f"profile fact not found: {fact_id}")
        return fact

    def list_profile_facts(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status and status not in VALID_PROFILE_STATUS:
            raise PlannerError("invalid profile fact status")
        now = self._now()
        result = self.store.list_profile_facts(status=status)
        for fact in result:
            valid_until = parse_datetime(
                fact.get("valid_until"),
                self.settings.timezone,
            )
            fact["currently_valid"] = (
                valid_until is None or valid_until > now
            )
        return result

    def confirm_profile_fact(self, fact_id: str) -> dict[str, Any]:
        try:
            fact = self.store.confirm_profile_fact(
                fact_id=fact_id,
                now=utc_iso(self._now()),
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if fact is None:
            raise PlannerError(f"profile fact not found: {fact_id}")
        return fact

    def reject_profile_fact(self, fact_id: str) -> dict[str, Any]:
        try:
            fact = self.store.reject_profile_fact(
                fact_id=fact_id,
                now=utc_iso(self._now()),
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if fact is None:
            raise PlannerError(f"profile fact not found: {fact_id}")
        return fact

    def revoke_profile_fact(self, fact_id: str) -> dict[str, Any]:
        try:
            fact = self.store.revoke_profile_fact(
                fact_id=fact_id,
                now=utc_iso(self._now()),
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if fact is None:
            raise PlannerError(f"profile fact not found: {fact_id}")
        return fact

    def generate_monitor_proposals(self) -> dict[str, Any]:
        now = self._now()
        confirmed = self.list_profile_facts(status="confirmed")
        current_facts = [
            fact
            for fact in confirmed
            if fact["currently_valid"]
        ]
        goals = self.list_goals(status="active", limit=500)
        generated = build_monitor_proposals(
            facts=current_facts,
            goals=goals,
        )
        stored = self.store.sync_monitor_proposals(
            proposals=generated,
            now=utc_iso(now),
        )
        current_keys = {
            proposal["proposal_key"] for proposal in generated
        }
        proposals = [
            proposal
            for proposal in stored
            if proposal["proposal_key"] in current_keys
        ]
        return {
            "generated_at": utc_iso(now),
            "network_scan_started": False,
            "requires_explicit_approval": True,
            "confirmed_fact_count": len(current_facts),
            "sensitive_fact_count_excluded": sum(
                1
                for fact in current_facts
                if fact["privacy"] == "sensitive"
            ),
            "active_goal_count": len(goals),
            "proposals": proposals,
        }

    def get_monitor_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.store.get_monitor_proposal(proposal_id)
        if proposal is None:
            raise PlannerError(
                f"monitor proposal not found: {proposal_id}"
            )
        proposal["network_scan_active"] = False
        return proposal

    def list_monitor_proposals(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status and status not in VALID_MONITOR_STATUS:
            raise PlannerError("invalid monitor proposal status")
        result = self.store.list_monitor_proposals(status=status)
        for proposal in result:
            proposal["network_scan_active"] = False
        return result

    def decide_monitor_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
    ) -> dict[str, Any]:
        normalized = _normalize_choice(
            decision,
            {"approved", "rejected"},
            field="monitor decision",
        )
        try:
            proposal = self.store.decide_monitor_proposal(
                proposal_id=proposal_id,
                decision=normalized,
                now=utc_iso(self._now()),
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if proposal is None:
            raise PlannerError(
                f"monitor proposal not found: {proposal_id}"
            )
        proposal["network_scan_active"] = False
        return proposal

    def confirm(self, item_id: str) -> dict[str, Any]:
        item = self.get(item_id)
        if item["status"] == "confirmed":
            return {**item, "nudges": self.store.list_nudges(item["id"])}
        if item["status"] != "draft":
            raise PlannerError(
                f"item {item['id']} cannot be confirmed from status {item['status']}"
            )
        if not any(item.get(key) for key in ("actionable_at", "due_at", "event_at")):
            raise PlannerError(
                "at least one of actionable_at, due_at, or event_at is required before confirmation"
            )
        now = self._now()
        nudges = self._build_nudges(item, now=now)
        confirmed = self.store.confirm_item(
            item["id"], updated_at=utc_iso(now), nudges=nudges
        )
        if confirmed is None:
            raise PlannerError(f"planner item not found: {item_id}")
        return {**confirmed, "nudges": self.store.list_nudges(item["id"])}

    def _build_nudges(self, item: dict[str, Any], *, now: datetime) -> list[dict[str, Any]]:
        candidates: list[tuple[str, datetime, str]] = []
        actionable = parse_datetime(item.get("actionable_at"), item["timezone"])
        due = parse_datetime(item.get("due_at"), item["timezone"])
        event = parse_datetime(item.get("event_at"), item["timezone"])

        if actionable:
            candidates.append(("actionable", actionable, "normal"))
        if due:
            for hours in self.settings.reminder_offsets_hours:
                severity = "urgent" if hours <= 3 else "high" if hours <= 24 else "normal"
                candidates.append((f"due-{hours}h", due - timedelta(hours=hours), severity))
        if event:
            candidates.append(("event-24h", event - timedelta(hours=24), "normal"))
            candidates.append(("event-2h", event - timedelta(hours=2), "high"))

        deduped: dict[str, tuple[str, datetime, str]] = {}
        for kind, fire_at, severity in candidates:
            effective = fire_at if fire_at > now else now
            key = utc_iso(effective)
            previous = deduped.get(key)
            if previous is None or _severity_rank(severity) > _severity_rank(previous[2]):
                deduped[key] = (kind, effective, severity)

        created = utc_iso(now)
        require_ack = item.get("importance") in {"high", "critical"}
        return [
            {
                "kind": kind,
                "fire_at": utc_iso(fire_at),
                "severity": severity,
                "ack_required": require_ack or severity in {"high", "urgent"},
                "idempotency_key": f"{item['id']}:{kind}:{utc_iso(fire_at)}",
                "created_at": created,
            }
            for _, (kind, fire_at, severity) in sorted(deduped.items())
        ]

    def acknowledge(self, item_id: str) -> dict[str, Any]:
        self.get(item_id)
        item = self.store.acknowledge_item(item_id, now=utc_iso(self._now()))
        if item is None:
            raise PlannerError(f"planner item not found: {item_id}")
        if item["status"] != "confirmed":
            raise PlannerError(f"only confirmed items can be acknowledged: {item_id}")
        return item

    def snooze(self, item_id: str, until: str | datetime) -> dict[str, Any]:
        item = self.get(item_id)
        if item["status"] != "confirmed":
            raise PlannerError(f"only confirmed items can be snoozed: {item_id}")
        parsed = parse_datetime(until, item["timezone"])
        now = self._now()
        if parsed is None or parsed <= now:
            raise PlannerError("snooze time must be in the future")
        result = self.store.snooze_item(
            item["id"], until=utc_iso(parsed), now=utc_iso(now)
        )
        assert result is not None
        return result

    def complete(self, item_id: str) -> dict[str, Any]:
        self.get(item_id)
        item = self.store.complete_item(item_id, now=utc_iso(self._now()))
        if item is None:
            raise PlannerError(f"planner item not found: {item_id}")
        return item

    def cancel(self, item_id: str) -> dict[str, Any]:
        self.get(item_id)
        try:
            item = self.store.cancel_item(item_id, now=utc_iso(self._now()))
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if item is None:
            raise PlannerError(f"planner item not found: {item_id}")
        return item

    def calendar_preview(
        self,
        item_id: str,
        *,
        calendar_id: str | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        item = self.get(item_id)
        if item["status"] != "confirmed":
            raise PlannerError("calendar sync is available only for confirmed items")
        resolved_calendar = str(
            calendar_id or self.settings.calendar_id or "primary"
        ).strip()
        resolved_kinds = self._calendar_kinds(kinds)
        links = {
            (link["provider"], link["calendar_id"], link["event_kind"]): link
            for link in self.store.list_calendar_links(item["id"])
        }
        proposals = event_payloads(
            item, kinds=resolved_kinds, local_iso_fn=local_iso
        )
        for proposal in proposals:
            link = links.get(
                ("google_workspace", resolved_calendar, proposal["kind"])
            )
            proposal["payload_hash"] = _payload_hash(proposal)
            proposal["sync_status"] = (
                "ready"
                if link is None
                else "linked"
                if link["status"] == "active"
                else "manual_update_required"
                if link["status"] == "stale"
                else "sync_in_progress"
                if link["status"] == "creating"
                else "previous_sync_failed"
            )
            if link:
                proposal["external_event_id"] = link["external_event_id"]
                proposal["html_link"] = link.get("html_link")
        return {
            "item_id": item["id"],
            "calendar_id": resolved_calendar,
            "provider": "google_workspace",
            "requires_explicit_confirmation": True,
            "events": proposals,
        }

    def calendar_sync(
        self,
        item_id: str,
        *,
        confirmed: bool,
        calendar_id: str | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
        client: Any | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            raise PlannerError(
                "calendar write requires explicit confirmation; preview it first"
            )
        preview = self.calendar_preview(
            item_id, calendar_id=calendar_id, kinds=kinds
        )
        blocked = [
            event
            for event in preview["events"]
            if event["sync_status"]
            in {
                "manual_update_required",
                "sync_in_progress",
                "previous_sync_failed",
            }
        ]
        if blocked:
            kinds_text = ", ".join(
                f"{event['kind']}={event['sync_status']}" for event in blocked
            )
            raise PlannerError(
                "calendar sync requires manual reconciliation before another write "
                f"({kinds_text})"
            )

        calendar_client = client or GoogleWorkspaceCalendar()
        created: list[dict[str, Any]] = []
        now_iso = utc_iso(self._now())
        for event in preview["events"]:
            if event["sync_status"] != "ready":
                continue
            reservation, acquired = self.store.reserve_calendar_link(
                item_id=preview["item_id"],
                provider=preview["provider"],
                calendar_id=preview["calendar_id"],
                event_kind=event["kind"],
                payload_hash=event["payload_hash"],
                now=now_iso,
            )
            if not acquired:
                raise PlannerError(
                    f"calendar event {event['kind']} was reserved by another sync"
                )
            try:
                external = calendar_client.create_event(
                    calendar_id=preview["calendar_id"],
                    summary=event["summary"],
                    start=event["start"],
                    end=event["end"],
                    description=event["description"],
                    location=event.get("location") or "",
                )
            except Exception as exc:
                self.store.fail_calendar_link(
                    link_id=reservation["id"], error=str(exc), now=utc_iso(self._now())
                )
                raise
            link = self.store.complete_calendar_link(
                link_id=reservation["id"],
                external_event_id=str(external["id"]),
                html_link=external.get("htmlLink"),
                now=utc_iso(self._now()),
            )
            created.append({**event, "calendar_link": link})
        return {
            **self.calendar_preview(
                item_id, calendar_id=preview["calendar_id"], kinds=kinds
            ),
            "created": created,
        }

    def calendar_forget(
        self,
        item_id: str,
        *,
        confirmed_external_cleanup: bool,
        calendar_id: str | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Forget local links only after the user reconciled external events."""
        self.get(item_id)
        if not confirmed_external_cleanup:
            raise PlannerError(
                "calendar-forget requires confirmation that linked external "
                "events were reviewed and removed or updated manually"
            )
        resolved_calendar = str(
            calendar_id or self.settings.calendar_id or "primary"
        ).strip()
        resolved_kinds = self._calendar_kinds(kinds)
        removed = self.store.delete_calendar_links(
            item_id=item_id,
            provider="google_workspace",
            calendar_id=resolved_calendar,
            event_kinds=resolved_kinds,
        )
        return {
            "item_id": item_id.upper(),
            "calendar_id": resolved_calendar,
            "removed_links": removed,
        }

    def _calendar_kinds(
        self, kinds: list[str] | tuple[str, ...] | None
    ) -> tuple[str, ...]:
        raw = kinds if kinds is not None else self.settings.calendar_kinds
        normalized = tuple(
            dict.fromkeys(str(kind).strip().lower() for kind in raw if str(kind).strip())
        )
        invalid = set(normalized) - VALID_CALENDAR_KINDS
        if invalid:
            raise PlannerError(
                f"invalid calendar kinds: {', '.join(sorted(invalid))}"
            )
        if not normalized:
            raise PlannerError("at least one calendar kind is required")
        return normalized

    def create_goal(
        self,
        *,
        title: str,
        area: str = "personal",
        outcome: str | None = None,
        target_date: str | datetime | None = None,
        weekly_target_minutes: int = 0,
        priority: str = "normal",
    ) -> dict[str, Any]:
        normalized_title = str(title or "").strip()
        if not normalized_title:
            raise PlannerError("goal title is required")
        normalized_priority = _normalize_choice(
            priority, VALID_IMPORTANCE, field="priority"
        )
        try:
            weekly_minutes = int(weekly_target_minutes)
        except (TypeError, ValueError) as exc:
            raise PlannerError("weekly_target_minutes must be an integer") from exc
        if not 0 <= weekly_minutes <= 7 * 24 * 60:
            raise PlannerError("weekly_target_minutes must be between 0 and 10080")
        parsed_target = parse_datetime(target_date, self.settings.timezone)
        now = utc_iso(self._now())
        return self.store.create_goal(
            {
                "title": normalized_title[:200],
                "area": str(area or "personal").strip().lower()[:80] or "personal",
                "outcome": str(outcome).strip() if outcome else None,
                "target_date": utc_iso(parsed_target) if parsed_target else None,
                "weekly_target_minutes": weekly_minutes,
                "priority": normalized_priority,
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
            }
        )

    def get_goal(self, goal_id: str) -> dict[str, Any]:
        goal = self.store.get_goal(goal_id)
        if goal is None:
            raise PlannerError(f"planner goal not found: {goal_id}")
        return goal

    def list_goals(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if status and status not in VALID_GOAL_STATUS:
            raise PlannerError("invalid goal status")
        return self.store.list_goals(status=status, limit=limit)

    def set_goal_status(self, goal_id: str, status: str) -> dict[str, Any]:
        self.get_goal(goal_id)
        normalized = _normalize_choice(status, VALID_GOAL_STATUS, field="status")
        goal = self.store.set_goal_status(
            goal_id, status=normalized, now=utc_iso(self._now())
        )
        assert goal is not None
        return goal

    def create_task(
        self,
        *,
        title: str | None = None,
        goal_id: str | None = None,
        planner_item_id: str | None = None,
        notes: str | None = None,
        earliest_start: str | datetime | None = None,
        due_at: str | datetime | None = None,
        estimated_minutes: int | None = None,
        min_block_minutes: int | None = None,
        splittable: bool = True,
        energy: str = "medium",
        priority: str | None = None,
    ) -> dict[str, Any]:
        goal = self.get_goal(goal_id) if goal_id else None
        if goal and goal["status"] != "active":
            raise PlannerError("new tasks can only be attached to active goals")
        item = self.get(planner_item_id) if planner_item_id else None
        normalized_title = str(title or (item or {}).get("title") or "").strip()
        if not normalized_title:
            raise PlannerError("task title is required")
        if item:
            earliest_start = (
                earliest_start
                if earliest_start is not None
                else item.get("actionable_at")
            )
            due_at = due_at if due_at is not None else item.get("due_at")
            priority = priority or item.get("importance")
        estimate = _bounded_minutes(
            estimated_minutes,
            default=self.settings.default_task_minutes,
            field="estimated_minutes",
            maximum=30 * 24 * 60,
        )
        minimum = _bounded_minutes(
            min_block_minutes,
            default=self.settings.default_min_block_minutes,
            field="min_block_minutes",
            maximum=8 * 60,
        )
        if minimum > estimate:
            raise PlannerError("min_block_minutes cannot exceed estimated_minutes")
        normalized_energy = _normalize_choice(energy, VALID_ENERGY, field="energy")
        normalized_priority = _normalize_choice(
            priority or "normal", VALID_IMPORTANCE, field="priority"
        )
        earliest = parse_datetime(earliest_start, self.settings.timezone)
        due = parse_datetime(due_at, self.settings.timezone)
        if earliest and due and earliest > due:
            raise PlannerError("earliest_start cannot be later than due_at")
        now = utc_iso(self._now())
        return self.store.create_task(
            {
                "goal_id": goal["id"] if goal else None,
                "planner_item_id": item["id"] if item else None,
                "title": normalized_title[:240],
                "notes": str(notes).strip() if notes else None,
                "earliest_start": utc_iso(earliest) if earliest else None,
                "due_at": utc_iso(due) if due else None,
                "estimated_minutes": estimate,
                "completed_minutes": 0,
                "min_block_minutes": minimum,
                "splittable": 1 if splittable else 0,
                "energy": normalized_energy,
                "priority": normalized_priority,
                "status": "backlog",
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
            }
        )

    def update_task(
        self,
        task_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.get_task(task_id)
        if current["status"] != "backlog":
            raise PlannerError(
                "only backlog tasks can be edited; cancel the active plan first"
            )
        allowed = {
            "goal_id",
            "title",
            "notes",
            "earliest_start",
            "due_at",
            "estimated_minutes",
            "min_block_minutes",
            "splittable",
            "energy",
            "priority",
        }
        unexpected = set(changes) - allowed
        if unexpected:
            raise PlannerError(
                f"unsupported task fields: {', '.join(sorted(unexpected))}"
            )
        values: dict[str, Any] = {}
        if "goal_id" in changes:
            goal_id = str(changes.get("goal_id") or "").strip()
            values["goal_id"] = self.get_goal(goal_id)["id"] if goal_id else None
        if "title" in changes:
            title = str(changes.get("title") or "").strip()
            if not title:
                raise PlannerError("task title is required")
            values["title"] = title[:240]
        if "notes" in changes:
            notes = changes.get("notes")
            values["notes"] = str(notes).strip() if notes else None

        earliest_source = changes.get("earliest_start", current.get("earliest_start"))
        due_source = changes.get("due_at", current.get("due_at"))
        earliest = parse_datetime(earliest_source, self.settings.timezone)
        due = parse_datetime(due_source, self.settings.timezone)
        if earliest and due and earliest > due:
            raise PlannerError("earliest_start cannot be later than due_at")
        if "earliest_start" in changes:
            values["earliest_start"] = utc_iso(earliest) if earliest else None
        if "due_at" in changes:
            values["due_at"] = utc_iso(due) if due else None

        estimate = _bounded_minutes(
            changes.get("estimated_minutes", current["estimated_minutes"]),
            default=int(current["estimated_minutes"]),
            field="estimated_minutes",
            maximum=30 * 24 * 60,
        )
        minimum = _bounded_minutes(
            changes.get("min_block_minutes", current["min_block_minutes"]),
            default=int(current["min_block_minutes"]),
            field="min_block_minutes",
            maximum=8 * 60,
        )
        if estimate < int(current["completed_minutes"]):
            raise PlannerError("estimated_minutes cannot be below completed progress")
        if minimum > estimate:
            raise PlannerError("min_block_minutes cannot exceed estimated_minutes")
        if "estimated_minutes" in changes:
            values["estimated_minutes"] = estimate
        if "min_block_minutes" in changes:
            values["min_block_minutes"] = minimum
        if "splittable" in changes:
            values["splittable"] = 1 if bool(changes["splittable"]) else 0
        if "energy" in changes:
            values["energy"] = _normalize_choice(
                changes.get("energy"), VALID_ENERGY, field="energy"
            )
        if "priority" in changes:
            values["priority"] = _normalize_choice(
                changes.get("priority"), VALID_IMPORTANCE, field="priority"
            )
        try:
            updated = self.store.update_task(
                task_id,
                values=values,
                now=utc_iso(self._now()),
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if updated is None:
            raise PlannerError(f"planner task not found: {task_id}")
        return self._task_with_remaining(updated)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        self.get_task(task_id)
        try:
            updated = self.store.cancel_task(task_id, now=utc_iso(self._now()))
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if updated is None:
            raise PlannerError(f"planner task not found: {task_id}")
        return self._task_with_remaining(updated)

    def get_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise PlannerError(f"planner task not found: {task_id}")
        return self._task_with_remaining(task)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        goal_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if status and status not in VALID_TASK_STATUS:
            raise PlannerError("invalid task status")
        if goal_id:
            self.get_goal(goal_id)
        return [
            self._task_with_remaining(task)
            for task in self.store.list_tasks(
                status=status, goal_id=goal_id, limit=limit
            )
        ]

    def add_task_progress(
        self, task_id: str, minutes: int, *, note: str | None = None
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] not in {"backlog", "planned"}:
            raise PlannerError("progress can only be added to open tasks")
        try:
            progress = int(minutes)
        except (TypeError, ValueError) as exc:
            raise PlannerError("progress minutes must be an integer") from exc
        if progress <= 0:
            raise PlannerError("progress minutes must be positive")
        updated = self.store.add_task_progress(
            task_id,
            minutes=progress,
            now=utc_iso(self._now()),
            note=note,
        )
        assert updated is not None
        return self._task_with_remaining(updated)

    def complete_task(
        self, task_id: str, *, note: str | None = None
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] not in {"backlog", "planned", "done"}:
            raise PlannerError("only open tasks can be completed")
        if task["status"] == "done":
            return task
        updated = self.store.complete_task(
            task_id, now=utc_iso(self._now()), note=note
        )
        assert updated is not None
        return self._task_with_remaining(updated)

    def add_availability(
        self,
        *,
        weekday: str | int,
        start_time: str,
        end_time: str,
        energy: str = "medium",
        label: str | None = None,
    ) -> dict[str, Any]:
        weekday_value = _parse_weekday(weekday)
        start_minute = _parse_clock(start_time)
        end_minute = _parse_clock(end_time, allow_24=True)
        if start_minute >= end_minute:
            raise PlannerError(
                "availability must end after it starts; overnight windows "
                "should be split across two weekdays"
            )
        normalized_energy = _normalize_choice(
            energy, VALID_ENERGY, field="energy"
        )
        for row in self.store.list_availability(weekday=weekday_value):
            if (
                int(row["enabled"])
                and start_minute < int(row["end_minute"])
                and end_minute > int(row["start_minute"])
            ):
                raise PlannerError(
                    f"availability overlaps existing window {row['id']}"
                )
        now = utc_iso(self._now())
        return self.store.add_availability(
            {
                "weekday": weekday_value,
                "start_minute": start_minute,
                "end_minute": end_minute,
                "energy": normalized_energy,
                "label": str(label).strip()[:120] if label else None,
                "enabled": 1,
                "created_at": now,
                "updated_at": now,
            }
        )

    def list_availability(
        self, *, weekday: str | int | None = None
    ) -> list[dict[str, Any]]:
        weekday_value = _parse_weekday(weekday) if weekday is not None else None
        result = self.store.list_availability(weekday=weekday_value)
        for row in result:
            row["weekday_name"] = _weekday_name(int(row["weekday"]))
            row["start_time"] = _format_clock(int(row["start_minute"]))
            row["end_time"] = _format_clock(int(row["end_minute"]))
        return result

    def clear_availability(
        self,
        *,
        availability_id: str | None = None,
        weekday: str | int | None = None,
    ) -> list[dict[str, Any]]:
        weekday_value = _parse_weekday(weekday) if weekday is not None else None
        if availability_id is None and weekday_value is None:
            raise PlannerError("availability_id or weekday is required")
        return self.store.delete_availability(
            availability_id=availability_id, weekday=weekday_value
        )

    def schedule_preview(
        self,
        *,
        days: int | None = None,
        start_at: str | datetime | None = None,
        replan: bool = False,
        use_calendar: bool | None = None,
        calendar_ids: list[str] | tuple[str, ...] | None = None,
        calendar_client: Any | None = None,
    ) -> dict[str, Any]:
        horizon = (
            self.settings.planning_horizon_days if days is None else int(days)
        )
        if not 1 <= horizon <= 42:
            raise PlannerError("schedule horizon must be between 1 and 42 days")
        now = self._now()
        parsed_start = parse_datetime(start_at, self.settings.timezone) or now
        window_start = max(parsed_start, now)
        window_end = window_start + timedelta(days=horizon)
        start_iso = utc_iso(window_start)
        end_iso = utc_iso(window_end)
        replacements = (
            self.store.overlapping_confirmed_plan_ids(
                window_start=start_iso, window_end=end_iso
            )
            if replan
            else []
        )
        busy_blocks = self.store.list_confirmed_plan_blocks(
            window_start=start_iso,
            window_end=end_iso,
            exclude_plan_ids=replacements,
        )
        all_future_blocks = self.store.list_confirmed_plan_blocks(
            window_start=start_iso,
            window_end="9999-12-31T23:59:59Z",
            exclude_plan_ids=replacements,
        )
        reserved_by_task: dict[str, int] = {}
        for block in all_future_blocks:
            reserved_by_task[block["task_id"]] = (
                reserved_by_task.get(block["task_id"], 0) + int(block["minutes"])
            )

        goals = self.store.list_goals(status="active", limit=500)
        active_goal_ids = {goal["id"] for goal in goals}
        tasks = []
        for task in self.store.list_tasks(limit=1000):
            if task["status"] not in {"backlog", "planned"}:
                continue
            if task.get("goal_id") and task["goal_id"] not in active_goal_ids:
                continue
            remaining = max(
                0,
                int(task["estimated_minutes"])
                - int(task["completed_minutes"])
                - reserved_by_task.get(task["id"], 0),
            )
            if remaining <= 0:
                continue
            goal = next(
                (goal for goal in goals if goal["id"] == task.get("goal_id")),
                None,
            )
            tasks.append(
                {
                    **task,
                    "due_at": task.get("due_at")
                    or (goal or {}).get("target_date"),
                    "remaining_minutes": remaining,
                    "timezone": self.settings.timezone,
                }
            )

        busy_intervals = []
        for block in busy_blocks:
            start = parse_datetime(block["starts_at"], self.settings.timezone)
            end = parse_datetime(block["ends_at"], self.settings.timezone)
            if start and end:
                busy_intervals.append((start, end))
        for item in self.store.list_items(status="confirmed", limit=500):
            event = parse_datetime(item.get("event_at"), item["timezone"])
            if event:
                busy_intervals.append(
                    (
                        event,
                        event
                        + timedelta(
                            minutes=int(item.get("event_duration_minutes") or 60)
                        ),
                    )
                )
        external_calendar = self._calendar_busy_snapshot(
            window_start=window_start,
            window_end=window_end,
            use_calendar=use_calendar,
            calendar_ids=calendar_ids,
            client=calendar_client,
        )
        for event in external_calendar["events"]:
            start = parse_datetime(event["starts_at"], self.settings.timezone)
            end = parse_datetime(event["ends_at"], self.settings.timezone)
            if start and end:
                busy_intervals.append((start, end))

        proposal = build_rolling_plan(
            tasks=tasks,
            goals=goals,
            availability=self.store.list_availability(),
            busy_intervals=busy_intervals,
            window_start=window_start,
            window_end=window_end,
            timezone_name=self.settings.timezone,
            buffer_percent=self.settings.planning_buffer_percent,
            max_block_minutes=self.settings.max_block_minutes,
        )
        summary = {
            key: value for key, value in proposal.items() if key != "blocks"
        }
        summary["external_calendar"] = external_calendar["summary"]
        plan = self.store.create_plan_draft(
            window_start=start_iso,
            window_end=end_iso,
            timezone_name=self.settings.timezone,
            is_replan=bool(replan),
            replaces_plan_ids=replacements,
            summary=summary,
            blocks=proposal["blocks"],
            now=utc_iso(now),
        )
        plan["requires_explicit_confirmation"] = True
        plan["confirmation_requires_replace"] = bool(replacements)
        return plan

    def daily_review(
        self,
        *,
        review_date: str | date | datetime | None = None,
        use_calendar: bool | None = None,
        calendar_ids: list[str] | tuple[str, ...] | None = None,
        calendar_client: Any | None = None,
        propose_replan: bool = False,
    ) -> dict[str, Any]:
        now = self._now()
        target_date, day_start, day_end = self._review_day_bounds(review_date)
        lookahead_end = max(
            day_end,
            now + timedelta(hours=self.settings.review_lookahead_hours),
        )
        day_blocks = self.store.list_plan_blocks_for_window(
            window_start=utc_iso(day_start),
            window_end=utc_iso(day_end),
        )
        future_blocks = self.store.list_plan_blocks_for_window(
            window_start=utc_iso(min(now, day_start)),
            window_end=utc_iso(lookahead_end),
        )
        progress = self.store.list_task_progress(
            window_start=utc_iso(day_start),
            window_end=utc_iso(day_end),
        )
        checkins = self.store.list_block_checkins(
            window_start=utc_iso(day_start),
            window_end=utc_iso(day_end),
        )
        external_calendar = self._calendar_busy_snapshot(
            window_start=min(now, day_start),
            window_end=lookahead_end,
            use_calendar=use_calendar,
            calendar_ids=calendar_ids,
            client=calendar_client,
        )
        report = build_daily_review(
            day_blocks=day_blocks,
            future_blocks=future_blocks,
            progress=progress,
            checkins=checkins,
            tasks=self.list_tasks(limit=1000),
            external_events=external_calendar["events"],
            now=now,
            day_start=day_start,
            day_end=day_end,
            lookahead_end=lookahead_end,
            timezone_name=self.settings.timezone,
            overdue_threshold_minutes=self.settings.replan_overdue_minutes,
        )
        result = {
            "date": target_date.isoformat(),
            "timezone": self.settings.timezone,
            "generated_at": utc_iso(now),
            "external_calendar": external_calendar["summary"],
            **report,
            "proposed_replan": None,
        }
        if propose_replan and report["replan_recommended"]:
            local_today = now.astimezone(
                resolve_timezone(self.settings.timezone)
            ).date()
            if target_date != local_today:
                raise PlannerError(
                    "a replan draft can only be proposed for the current local day"
                )
            result["proposed_replan"] = self.schedule_preview(
                start_at=now,
                replan=True,
                use_calendar=use_calendar,
                calendar_ids=calendar_ids,
                calendar_client=calendar_client,
            )
        return result

    def _calendar_busy_snapshot(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        use_calendar: bool | None,
        calendar_ids: list[str] | tuple[str, ...] | None,
        client: Any | None,
        require_live: bool = False,
    ) -> dict[str, Any]:
        enabled = (
            self.settings.calendar_busy_read
            if use_calendar is None
            else bool(use_calendar)
        )
        calendars = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in (
                    calendar_ids
                    if calendar_ids is not None
                    else self.settings.calendar_busy_ids
                )
                if str(value).strip()
            )
        )
        if not enabled:
            return {
                "events": [],
                "summary": {
                    "enabled": False,
                    "status": "disabled",
                    "calendar_ids": list(calendars),
                    "event_count": 0,
                    "warnings": [],
                    "snapshot_hash": None,
                },
            }
        if not calendars:
            calendars = (self.settings.calendar_id or "primary",)

        adapter = client or GoogleWorkspaceCalendar()
        now = self._now()
        fetched_at = utc_iso(now)
        expires_at = utc_iso(
            now + timedelta(minutes=self.settings.calendar_busy_cache_minutes)
        )
        start_iso = utc_iso(window_start)
        end_iso = utc_iso(window_end)
        events: list[dict[str, Any]] = []
        warnings: list[str] = []
        calendar_states = []
        for calendar_id in calendars:
            try:
                raw_events = adapter.list_events(
                    calendar_id=calendar_id,
                    start=start_iso,
                    end=end_iso,
                    max_results=2500,
                )
                normalized = normalize_busy_events(
                    raw_events,
                    calendar_id=calendar_id,
                    timezone_name=self.settings.timezone,
                    parse_datetime_fn=parse_datetime,
                    utc_iso_fn=utc_iso,
                )
                calendar_hash = _calendar_snapshot_hash(
                    normalized, calendar_ids=(calendar_id,)
                )
                cached = self.store.replace_calendar_busy_cache(
                    provider="google_workspace",
                    calendar_id=calendar_id,
                    window_start=start_iso,
                    window_end=end_iso,
                    events=normalized,
                    fetched_at=fetched_at,
                    expires_at=expires_at,
                    snapshot_hash=calendar_hash,
                )
                events.extend(cached)
                calendar_states.append(
                    {
                        "calendar_id": calendar_id,
                        "status": "live",
                        "event_count": len(cached),
                        "fetched_at": fetched_at,
                    }
                )
            except Exception as exc:
                if require_live:
                    raise PlannerError(
                        f"cannot validate calendar {calendar_id} before "
                        f"schedule confirmation: {str(exc)[:300]}"
                    ) from exc
                cached = self.store.list_cached_calendar_busy(
                    provider="google_workspace",
                    calendar_ids=(calendar_id,),
                    window_start=start_iso,
                    window_end=end_iso,
                )
                sync_state = self.store.get_calendar_sync_state(
                    provider="google_workspace",
                    calendar_id=calendar_id,
                    window_start=start_iso,
                    window_end=end_iso,
                )
                state_expiry = parse_datetime(
                    (sync_state or {}).get("expires_at"),
                    self.settings.timezone,
                )
                is_fresh = state_expiry is not None and state_expiry > now
                has_cache_result = sync_state is not None or bool(cached)
                state = (
                    "cache_fresh"
                    if is_fresh
                    else "cache_stale"
                    if has_cache_result
                    else "unavailable"
                )
                events.extend(cached)
                warning = (
                    f"calendar {calendar_id} read failed; "
                    f"{'using cached busy result' if has_cache_result else 'no cached busy result available'}: "
                    f"{str(exc)[:300]}"
                )
                warnings.append(warning)
                calendar_states.append(
                    {
                        "calendar_id": calendar_id,
                        "status": state,
                        "event_count": (
                            int(sync_state["event_count"])
                            if sync_state is not None
                            else len(cached)
                        ),
                        "fetched_at": (
                            sync_state["fetched_at"]
                            if sync_state is not None
                            else max(
                                (row["fetched_at"] for row in cached),
                                default=None,
                            )
                        ),
                        "error": str(exc)[:300],
                    }
                )

        deduped = {
            (event["calendar_id"], event["event_id"]): {
                **event,
                "all_day": bool(event.get("all_day")),
            }
            for event in events
        }
        states = {entry["status"] for entry in calendar_states}
        if states == {"live"}:
            status = "live"
        elif "unavailable" in states and len(states) == 1:
            status = "unavailable"
        elif "cache_stale" in states and states <= {"cache_stale"}:
            status = "stale"
        elif states <= {"cache_fresh"}:
            status = "cached"
        else:
            status = "mixed"
        values = sorted(
            deduped.values(),
            key=lambda event: (
                event["starts_at"],
                event["calendar_id"],
                event["event_id"],
            ),
        )
        snapshot_hash = _calendar_snapshot_hash(
            values, calendar_ids=calendars
        )
        return {
            "events": values,
            "summary": {
                "enabled": True,
                "status": status,
                "calendar_ids": list(calendars),
                "event_count": len(values),
                "calendars": calendar_states,
                "warnings": warnings,
                "window_start": start_iso,
                "window_end": end_iso,
                "snapshot_hash": snapshot_hash,
            },
        }

    def calendar_busy(
        self,
        *,
        start_at: str | datetime | None = None,
        end_at: str | datetime | None = None,
        calendar_ids: list[str] | tuple[str, ...] | None = None,
        client: Any | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        start = parse_datetime(start_at, self.settings.timezone) or now
        end = parse_datetime(end_at, self.settings.timezone) or (
            start + timedelta(days=self.settings.planning_horizon_days)
        )
        if end <= start:
            raise PlannerError("calendar busy end must be later than start")
        return self._calendar_busy_snapshot(
            window_start=start,
            window_end=end,
            use_calendar=True,
            calendar_ids=calendar_ids,
            client=client,
        )

    def _review_day_bounds(
        self, value: str | date | datetime | None
    ) -> tuple[date, datetime, datetime]:
        timezone_value = resolve_timezone(self.settings.timezone)
        if value is None:
            target = self._now().astimezone(timezone_value).date()
        elif isinstance(value, datetime):
            parsed = parse_datetime(value, self.settings.timezone)
            assert parsed is not None
            target = parsed.astimezone(timezone_value).date()
        elif isinstance(value, date):
            target = value
        else:
            raw = str(value).strip()
            try:
                target = date.fromisoformat(raw)
            except ValueError:
                parsed = parse_datetime(raw, self.settings.timezone)
                if parsed is None:
                    raise PlannerError("review_date is required")
                target = parsed.astimezone(timezone_value).date()
        start = datetime.combine(target, time.min, timezone_value).astimezone(
            timezone.utc
        )
        end = datetime.combine(
            target + timedelta(days=1), time.min, timezone_value
        ).astimezone(timezone.utc)
        return target, start, end

    def get_schedule(self, plan_id: str) -> dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise PlannerError(f"planner schedule not found: {plan_id}")
        return plan

    def list_schedules(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_plans(limit=limit)

    def get_block(self, block_id: str) -> dict[str, Any]:
        block = self.store.get_plan_block(block_id)
        if block is None:
            raise PlannerError(f"planner block not found: {block_id}")
        return block

    def checkin_block(
        self,
        block_id: str,
        *,
        outcome: str,
        actual_minutes: int | None = None,
        note: str | None = None,
        propose_replan: bool = False,
    ) -> dict[str, Any]:
        normalized_outcome = _normalize_choice(
            outcome,
            {"done", "skipped", "deferred"},
            field="block outcome",
        )
        if propose_replan and normalized_outcome not in {"skipped", "deferred"}:
            raise PlannerError(
                "a replan proposal is only available for skipped or "
                "deferred blocks"
            )
        block = self.get_block(block_id)
        if block["checkin"] is not None:
            existing = block["checkin"]
            if (
                existing["outcome"] == normalized_outcome
                and (
                    normalized_outcome != "done"
                    or actual_minutes is None
                    or int(existing["actual_minutes"]) == int(actual_minutes)
                )
            ):
                return {
                    "block": block,
                    "replan_recommended": normalized_outcome
                    in {"skipped", "deferred"},
                    "proposed_replan": None,
                }
            raise PlannerError(
                f"block {block['id']} was already checked in as "
                f"{existing['outcome']}"
            )
        if normalized_outcome == "done":
            if actual_minutes is None:
                actual = int(block["minutes"])
            else:
                try:
                    actual = int(actual_minutes)
                except (TypeError, ValueError) as exc:
                    raise PlannerError(
                        "actual_minutes must be an integer"
                    ) from exc
                if not 1 <= actual <= 24 * 60:
                    raise PlannerError(
                        "actual_minutes must be between 1 and 1440"
                    )
        else:
            if actual_minutes not in {None, 0}:
                raise PlannerError(
                    "actual_minutes is only valid for a completed block"
                )
            actual = None
        try:
            updated = self.store.checkin_plan_block(
                block_id=block["id"],
                outcome=normalized_outcome,
                actual_minutes=actual,
                note=note,
                now=utc_iso(self._now()),
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if updated is None:
            raise PlannerError(f"planner block not found: {block_id}")
        proposed = None
        if propose_replan:
            proposed = self.schedule_preview(
                start_at=self._now(),
                replan=True,
            )
        return {
            "block": updated,
            "replan_recommended": normalized_outcome
            in {"skipped", "deferred"},
            "proposed_replan": proposed,
        }

    def schedule_calendar_preview(
        self,
        plan_id: str,
        *,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        preview = self._build_schedule_calendar_preview(
            plan_id,
            calendar_id=calendar_id,
        )
        snapshot_hash = _plan_calendar_preview_hash(preview)
        saved = self.store.save_plan_calendar_preview(
            plan_id=preview["plan_id"],
            provider=preview["provider"],
            calendar_id=preview["calendar_id"],
            snapshot_hash=snapshot_hash,
            event_count=len(preview["events"]),
            now=utc_iso(self._now()),
        )
        return {
            **preview,
            "snapshot_hash": snapshot_hash,
            "previewed_at": saved["previewed_at"],
        }

    def _build_schedule_calendar_preview(
        self,
        plan_id: str,
        *,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        plan = self.get_schedule(plan_id)
        if plan["status"] not in {"confirmed", "superseded"}:
            raise PlannerError(
                "schedule calendar preview requires a confirmed or "
                "superseded schedule"
            )
        resolved_calendar = str(
            calendar_id or self.settings.calendar_id or "primary"
        ).strip()
        links = {
            (link["block_id"], link["provider"], link["calendar_id"]): link
            for link in self.store.list_plan_block_calendar_links(
                plan_id=plan["id"]
            )
        }
        events = []
        for block in plan["blocks"]:
            if block["status"] != "confirmed":
                continue
            proposal = plan_block_event_payload(
                block,
                timezone_name=plan["timezone"],
                local_iso_fn=local_iso,
            )
            proposal["payload_hash"] = _payload_hash(proposal)
            link = links.get(
                (block["id"], "google_workspace", resolved_calendar)
            )
            if link is None:
                proposal["sync_status"] = "ready"
            elif link["status"] == "active":
                proposal["sync_status"] = (
                    "linked"
                    if link["payload_hash"] == proposal["payload_hash"]
                    else "manual_update_required"
                )
            elif link["status"] == "stale":
                proposal["sync_status"] = "manual_update_required"
            elif link["status"] == "creating":
                proposal["sync_status"] = "sync_in_progress"
            else:
                proposal["sync_status"] = "previous_sync_failed"
            if link:
                proposal["external_event_id"] = link["external_event_id"]
                proposal["html_link"] = link.get("html_link")
            events.append(proposal)
        stale_links = [
            link
            for link in links.values()
            if link["provider"] == "google_workspace"
            and link["calendar_id"] == resolved_calendar
            and link["status"] == "stale"
        ]
        return {
            "plan_id": plan["id"],
            "plan_status": plan["status"],
            "calendar_id": resolved_calendar,
            "provider": "google_workspace",
            "requires_explicit_confirmation": True,
            "events": events,
            "stale_links": stale_links,
        }

    def schedule_calendar_sync(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        calendar_id: str | None = None,
        client: Any | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            raise PlannerError(
                "schedule calendar write requires explicit confirmation; "
                "preview it first"
            )
        preview = self._build_schedule_calendar_preview(
            plan_id, calendar_id=calendar_id
        )
        saved_preview = self.store.get_plan_calendar_preview(
            plan_id=preview["plan_id"],
            provider=preview["provider"],
            calendar_id=preview["calendar_id"],
        )
        if saved_preview is None:
            raise PlannerError(
                "schedule calendar sync requires a prior "
                "schedule-calendar-preview"
            )
        current_hash = _plan_calendar_preview_hash(preview)
        if (
            saved_preview["snapshot_hash"] != current_hash
            or int(saved_preview["event_count"]) != len(preview["events"])
        ):
            raise PlannerError(
                "the confirmed plan changed after its calendar preview; "
                "preview it again before syncing"
            )
        if preview["plan_status"] != "confirmed":
            raise PlannerError(
                "only a currently confirmed schedule can create calendar events"
            )
        blocked = [
            event
            for event in preview["events"]
            if event["sync_status"]
            in {
                "manual_update_required",
                "sync_in_progress",
                "previous_sync_failed",
            }
        ]
        if blocked or preview["stale_links"]:
            detail = ", ".join(
                f"{event['block_id']}={event['sync_status']}"
                for event in blocked
            )
            raise PlannerError(
                "schedule calendar sync requires manual reconciliation "
                f"before another write ({detail or 'stale links'})"
            )
        calendar_client = client or GoogleWorkspaceCalendar()
        created = []
        for event in preview["events"]:
            if event["sync_status"] != "ready":
                continue
            try:
                reservation, acquired = (
                    self.store.reserve_plan_block_calendar_link(
                        block_id=event["block_id"],
                        provider=preview["provider"],
                        calendar_id=preview["calendar_id"],
                        payload_hash=event["payload_hash"],
                        now=utc_iso(self._now()),
                    )
                )
            except ValueError as exc:
                raise PlannerError(str(exc)) from exc
            if not acquired:
                raise PlannerError(
                    f"calendar event for block {event['block_id']} "
                    "was reserved by another sync"
                )
            try:
                external = calendar_client.create_event(
                    calendar_id=preview["calendar_id"],
                    summary=event["summary"],
                    start=event["start"],
                    end=event["end"],
                    description=event["description"],
                    location=event.get("location") or "",
                )
            except Exception as exc:
                self.store.fail_plan_block_calendar_link(
                    link_id=reservation["id"],
                    error=str(exc),
                    now=utc_iso(self._now()),
                )
                raise
            link = self.store.complete_plan_block_calendar_link(
                link_id=reservation["id"],
                external_event_id=str(external["id"]),
                html_link=external.get("htmlLink"),
                now=utc_iso(self._now()),
            )
            created.append({**event, "calendar_link": link})
            if link["status"] != "active":
                raise PlannerError(
                    f"block {event['block_id']} changed during calendar "
                    "creation; the external event was recorded as stale and "
                    "requires manual reconciliation"
                )
        return {
            **self._build_schedule_calendar_preview(
                plan_id, calendar_id=preview["calendar_id"]
            ),
            "snapshot_hash": saved_preview["snapshot_hash"],
            "previewed_at": saved_preview["previewed_at"],
            "created": created,
        }

    def schedule_calendar_forget(
        self,
        plan_id: str,
        *,
        confirmed_external_cleanup: bool,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_schedule(plan_id)
        if not confirmed_external_cleanup:
            raise PlannerError(
                "schedule-calendar-forget requires confirmation that all "
                "linked external events were reviewed and reconciled"
            )
        resolved_calendar = str(
            calendar_id or self.settings.calendar_id or "primary"
        ).strip()
        removed = self.store.delete_plan_block_calendar_links(
            plan_id=plan_id,
            provider="google_workspace",
            calendar_id=resolved_calendar,
        )
        self.store.delete_plan_calendar_preview(
            plan_id=plan_id,
            provider="google_workspace",
            calendar_id=resolved_calendar,
        )
        return {
            "plan_id": plan_id.upper(),
            "calendar_id": resolved_calendar,
            "removed_links": removed,
        }

    def confirm_schedule(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        replace: bool = False,
        calendar_client: Any | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            raise PlannerError(
                "schedule confirmation requires explicit approval of the preview"
            )
        current = self.get_schedule(plan_id)
        if current["status"] == "confirmed":
            return current
        if current["status"] == "draft":
            now = self._now()
            created_at = parse_datetime(
                current["created_at"], self.settings.timezone
            )
            if (
                created_at is None
                or now - created_at
                > timedelta(minutes=self.settings.schedule_draft_ttl_minutes)
            ):
                raise PlannerError(
                    "schedule draft expired; generate a fresh preview"
                )
            calendar_input = current["summary"].get("external_calendar") or {}
            if calendar_input.get("enabled"):
                expected_hash = str(
                    calendar_input.get("snapshot_hash") or ""
                )
                if not expected_hash:
                    raise PlannerError(
                        "schedule draft lacks a calendar snapshot; "
                        "generate a fresh preview"
                    )
                start = parse_datetime(
                    current["window_start"], self.settings.timezone
                )
                end = parse_datetime(
                    current["window_end"], self.settings.timezone
                )
                assert start is not None and end is not None
                live = self._calendar_busy_snapshot(
                    window_start=start,
                    window_end=end,
                    use_calendar=True,
                    calendar_ids=calendar_input.get("calendar_ids"),
                    client=calendar_client,
                    require_live=True,
                )
                if live["summary"]["snapshot_hash"] != expected_hash:
                    raise PlannerError(
                        "calendar changed after schedule preview; "
                        "generate a fresh preview before confirming"
                    )
        try:
            plan = self.store.confirm_plan(
                plan_id, replace=replace, now=utc_iso(self._now())
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if plan is None:
            raise PlannerError(f"planner schedule not found: {plan_id}")
        return plan

    def cancel_schedule(self, plan_id: str) -> dict[str, Any]:
        self.get_schedule(plan_id)
        try:
            plan = self.store.cancel_plan(plan_id, now=utc_iso(self._now()))
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if plan is None:
            raise PlannerError(f"planner schedule not found: {plan_id}")
        return plan

    def _task_with_remaining(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            **task,
            "remaining_minutes": max(
                0,
                int(task["estimated_minutes"]) - int(task["completed_minutes"]),
            ),
        }

    def today(self) -> list[dict[str, Any]]:
        now = self._now()
        result: list[dict[str, Any]] = []
        for item in self.store.list_items(status="confirmed", limit=500):
            item_timezone = resolve_timezone(item["timezone"])
            local_date = now.astimezone(item_timezone).date()
            for key in ("actionable_at", "due_at", "event_at"):
                parsed = parse_datetime(item.get(key), item["timezone"])
                if parsed and parsed.astimezone(item_timezone).date() == local_date:
                    result.append(item)
                    break
        return result

    def record_event(
        self,
        *,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        occurred_at: str | datetime | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(event_type or "").strip()
        normalized_source = str(source or "").strip()
        if not normalized_type or not normalized_source:
            raise PlannerError("event_type and source are required")
        if not isinstance(payload, dict):
            raise PlannerError("event payload must be an object")
        occurred = parse_datetime(occurred_at, self.settings.timezone) or self._now()
        key = str(dedupe_key or "").strip() or _payload_hash(
            {
                "event_type": normalized_type,
                "source": normalized_source,
                "payload": payload,
            }
        )
        return self.store.create_planner_event(
            event_type=normalized_type,
            source=normalized_source,
            dedupe_key=key,
            payload=dict(payload),
            occurred_at=utc_iso(occurred),
        )

    def _expert_snapshot(self, domains: list[str]) -> dict[str, Any]:
        packages = self.store.list_work_packages(limit=100)
        return {
            "profile": [
                fact
                for fact in self.list_profile_facts(status="confirmed")
                if fact.get("privacy_level", "standard") == "standard"
                and fact.get("currently_valid", True)
            ][:50],
            "goals": self.list_goals(status="active", limit=50),
            "availability": self.store.list_availability()[:50],
            "work_packages": [
                package
                for package in packages
                if package["domain"] in domains
                and package["status"] in {"proposed", "active"}
            ][:100],
        }

    def save_work_package(self, package: dict[str, Any]) -> dict[str, Any]:
        validated = validate_work_package(package)
        return self.store.save_work_package(validated, now=utc_iso(self._now()))

    def analyze_event(
        self, event_id: str, *, domains: list[str], expert_runner: Any
    ) -> dict[str, Any]:
        event = self.store.get_planner_event(event_id)
        if event is None:
            raise PlannerError(f"planner event not found: {event_id}")
        selected = list(
            dict.fromkeys(domain for domain in domains if domain in VALID_DOMAINS)
        )
        if not selected:
            raise PlannerError("at least one valid Planner domain is required")
        snapshot = self._expert_snapshot(selected)
        analyses: list[dict[str, Any]] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        started = perf_counter()
        try:
            for domain in selected:
                analyzed = expert_runner.analyze(
                    domain, event=event, snapshot=snapshot
                )
                proposed = dict(analyzed["package"])
                proposed["source_event_ids"] = list(
                    dict.fromkeys(
                        [*proposed.get("source_event_ids", []), event["id"]]
                    )
                )
                package = calibrate_agent_work_package(
                    proposed,
                    observations=self.store.list_estimate_observations(),
                    enabled=self.settings.estimate_learning_enabled,
                )
                analysis_usage = dict(analyzed.get("usage") or {})
                analyses.append(
                    {
                        "domain": domain,
                        "package": package,
                        "provider": analyzed.get("provider"),
                        "model": analyzed.get("model"),
                        "usage": analysis_usage,
                    }
                )
                for key in usage:
                    usage[key] += int(analysis_usage.get(key) or 0)
        except Exception as exc:
            failed_at = utc_iso(self._now())
            self.store.set_planner_event_status(
                event["id"], status="failed", processed_at=None
            )
            self.store.save_agent_run(
                trigger_event_id=event_id,
                selected_experts=selected,
                expert_result_ids=[],
                decision={"kind": "analysis_failed"},
                draft_plan_id=None,
                approval_status="not_requested",
                outcome="failed",
                reasoning_summary=f"{type(exc).__name__}: {str(exc)[:300]}",
                usage=usage,
                now=failed_at,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            raise
        return self.store.save_expert_analysis_batch(
            event_id=event["id"],
            selected_experts=selected,
            routing_rationale=explain_domain_routing(
                str(event["payload"].get("text") or ""), event["source"], selected
            ),
            analyses=analyses,
            usage=usage,
            duration_ms=round((perf_counter() - started) * 1000),
            now=utc_iso(self._now()),
        )

    def prepare_delegated_analysis(
        self, event_id: str, *, domains: list[str]
    ) -> dict[str, Any]:
        event = self.store.get_planner_event(event_id)
        if event is None:
            raise PlannerError(f"planner event not found: {event_id}")
        selected = list(
            dict.fromkeys(domain for domain in domains if domain in VALID_DOMAINS)
        )
        if not selected:
            raise PlannerError("at least one valid Planner domain is required")
        snapshot = self._expert_snapshot(selected)
        return {
            "event_id": event["id"],
            "delegations": [
                {
                    "domain": domain,
                    "goal": build_delegated_expert_goal(
                        domain, event=event, snapshot=snapshot
                    ),
                }
                for domain in selected
            ],
            "next_action": (
                "Call Hermes delegate_task once per delegation, then pass each "
                "plain JSON result to personal_planner expert_ingest."
            ),
        }

    def ingest_delegated_analysis(
        self, event_id: str, *, domain: str, raw_json: str
    ) -> dict[str, Any]:
        event = self.store.get_planner_event(event_id)
        if event is None:
            raise PlannerError(f"planner event not found: {event_id}")
        package = validate_delegated_expert_result(domain, raw_json)
        package["source_event_ids"] = list(
            dict.fromkeys([*package.get("source_event_ids", []), event["id"]])
        )
        package = calibrate_agent_work_package(
            package,
            observations=self.store.list_estimate_observations(),
            enabled=self.settings.estimate_learning_enabled,
        )
        now = utc_iso(self._now())
        result = self.store.save_expert_analysis_batch(
            event_id=event["id"],
            selected_experts=[domain],
            routing_rationale=explain_domain_routing(
                str(event["payload"].get("text") or ""), event["source"], [domain]
            ),
            analyses=[
                {
                    "domain": domain,
                    "package": package,
                    "provider": "hermes_delegate_task",
                    "model": None,
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                }
            ],
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            duration_ms=None,
            now=now,
        )
        return {
            "expert_result": result["expert_results"][0],
            "work_package": result["work_packages"][0],
            "agent_run": result["agent_run"],
        }

    def _milestone_fixed_commitments(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        external_events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = [
            {
                "id": block["id"],
                "starts_at": block["starts_at"],
                "ends_at": block["ends_at"],
                "title": block.get("title") or "Confirmed plan block",
            }
            for block in self.store.list_confirmed_plan_blocks(
                window_start=utc_iso(window_start),
                window_end=utc_iso(window_end),
                exclude_plan_ids=[],
            )
        ]
        for item in self.store.list_items(status="confirmed", limit=500):
            start = parse_datetime(item.get("event_at"), item["timezone"])
            if start is None or start >= window_end:
                continue
            end = start + timedelta(
                minutes=int(item.get("event_duration_minutes") or 60)
            )
            if end <= window_start:
                continue
            result.append(
                {
                    "id": item["id"],
                    "starts_at": utc_iso(start),
                    "ends_at": utc_iso(end),
                    "title": item["title"],
                }
            )
        result.extend(
            {
                "id": str(event.get("id") or event.get("event_id") or "calendar"),
                "starts_at": event["starts_at"],
                "ends_at": event["ends_at"],
                "title": event.get("title") or event.get("summary") or "Calendar commitment",
            }
            for event in external_events
        )
        return sorted(result, key=lambda value: (value["starts_at"], value["id"]))

    def milestone_preview(
        self,
        *,
        days: int | None = None,
        start_at: str | datetime | None = None,
        use_calendar: bool | None = None,
        calendar_ids: list[str] | tuple[str, ...] | None = None,
        calendar_client: Any | None = None,
        base_plan_id: str | None = None,
    ) -> dict[str, Any]:
        horizon = self.settings.planning_horizon_days if days is None else int(days)
        if not 1 <= horizon <= 42:
            raise PlannerError("milestone horizon must be between 1 and 42 days")
        now = self._now()
        parsed_start = parse_datetime(start_at, self.settings.timezone) or now
        window_start = max(parsed_start, now)
        window_end = window_start + timedelta(days=horizon)
        external = self._calendar_busy_snapshot(
            window_start=window_start,
            window_end=window_end,
            use_calendar=use_calendar,
            calendar_ids=calendar_ids,
            client=calendar_client,
        )
        capacities = build_daily_capacity(
            availability=self.store.list_availability(),
            fixed_commitments=self._milestone_fixed_commitments(
                window_start=window_start,
                window_end=window_end,
                external_events=external["events"],
            ),
            window_start=window_start,
            window_end=window_end,
            timezone_name=self.settings.timezone,
            buffer_percent=self.settings.daily_buffer_percent,
            recovery_minutes=self.settings.reminder_recovery_minutes,
        )
        packages = [
            package
            for package in self.store.list_work_packages(limit=500)
            if package["status"] in {"proposed", "active"}
        ]
        if not packages:
            raise PlannerError("no proposed or active work packages are available")
        package_ids = tuple(package["id"] for package in packages)
        proposal = build_milestone_plan(
            work_packages=packages,
            capacities=capacities,
            progress_observations=self.store.list_estimate_observations(
                work_package_ids=package_ids
            ),
            timezone_name=self.settings.timezone,
            max_daily_domains=self.settings.max_daily_domains,
            protect_hard_deadline_day=self.settings.protect_hard_deadline_day,
        )
        if base_plan_id is None:
            active_plans = self.store.list_milestone_plans(
                status="confirmed", limit=1
            )
            base_plan_id = active_plans[0]["id"] if active_plans else None
        current = self.store.get_milestone_plan(base_plan_id) if base_plan_id else None
        if current is not None and current["status"] != "confirmed":
            raise PlannerError("base milestone plan must be confirmed")
        summary = {key: value for key, value in proposal.items() if key != "milestones"}
        summary.update(
            {
                "base_plan_id": base_plan_id,
                "diff": diff_milestone_plans(current, proposal),
                "external_calendar": external["summary"],
            }
        )
        draft = self.store.create_milestone_plan_draft(
            window_start=utc_iso(window_start),
            window_end=utc_iso(window_end),
            timezone=self.settings.timezone,
            summary=summary,
            milestones=proposal["milestones"],
            now=utc_iso(now),
        )
        self.store.save_agent_run(
            trigger_event_id=None,
            selected_experts=[],
            expert_result_ids=[],
            decision={
                "kind": "milestone_plan_proposed",
                "allocation_policy": summary["decision_policy"],
                "diff": summary["diff"],
                "tradeoff_options": summary["tradeoff_options"],
            },
            draft_plan_id=draft["id"],
            approval_status="pending",
            outcome="draft_created",
            reasoning_summary=(
                "Deterministic aggregator created a milestone plan draft"
            ),
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            now=utc_iso(now),
        )
        draft["requires_explicit_confirmation"] = True
        draft["confirmation_requires_replace"] = base_plan_id is not None
        return draft

    def confirm_milestone_plan(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        replace_plan_id: str | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            raise PlannerError(
                "milestone plan confirmation requires explicit approval"
            )
        try:
            plan = self.store.confirm_milestone_plan(
                plan_id,
                replace_plan_id=replace_plan_id,
                now=utc_iso(self._now()),
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if plan is None:
            raise PlannerError(f"milestone plan not found: {plan_id}")
        return plan

    def reject_milestone_plan(self, plan_id: str) -> dict[str, Any]:
        try:
            plan = self.store.reject_milestone_plan(
                plan_id, now=utc_iso(self._now())
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if plan is None:
            raise PlannerError(f"milestone plan not found: {plan_id}")
        return plan

    def _estimate_observations(
        self, milestone: dict[str, Any], checkin: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if checkin["outcome"] not in {"done", "partial"}:
            return []
        step_ids = list(milestone.get("source_step_ids") or [])
        if len(step_ids) != 1:
            raise PlannerError(
                "a check-in milestone must reference exactly one source step"
            )
        package = self.store.get_work_package(milestone["work_package_id"])
        if package is None:
            raise PlannerError("milestone work package no longer exists")
        step = next(
            (
                value
                for value in package["steps"]
                if value["local_id"] == step_ids[0]
            ),
            None,
        )
        if step is None:
            raise PlannerError("milestone source step no longer exists")
        fraction = (
            1.0
            if checkin["outcome"] == "done"
            else float(checkin["completion_fraction"])
        )
        credited_p50 = min(
            int(step["p50_minutes"]),
            max(1, round(int(milestone["target_minutes"]) * fraction)),
        )
        credited_p90 = min(
            int(step["p90_minutes"]),
            max(
                credited_p50,
                round(
                    credited_p50
                    * int(step["p90_minutes"])
                    / int(step["p50_minutes"])
                ),
            ),
        )
        adjustment_p50 = 0
        if checkin["outcome"] == "partial":
            target = int(milestone["target_minutes"])
            predicted_remaining = (
                int(checkin["actual_minutes"]) / fraction
            ) * (1.0 - fraction)
            baseline_remaining = target * (1.0 - fraction)
            adjustment_limit = max(5, round(target * 0.5))
            adjustment_p50 = round(predicted_remaining - baseline_remaining)
            adjustment_p50 = max(
                -adjustment_limit, min(adjustment_limit, adjustment_p50)
            )
        adjustment_p90 = round(
            adjustment_p50
            * int(step["p90_minutes"])
            / int(step["p50_minutes"])
        )
        return [
            {
                "work_package_id": package["id"],
                "source_step_id": step["local_id"],
                "allocation_key": milestone["allocation_key"],
                "estimated_p50_minutes": int(milestone["target_minutes"]),
                "estimated_p90_minutes": credited_p90,
                "credited_p50_minutes": credited_p50,
                "credited_p90_minutes": credited_p90,
                "remaining_adjustment_p50_minutes": adjustment_p50,
                "remaining_adjustment_p90_minutes": adjustment_p90,
                "actual_minutes": checkin.get("actual_minutes"),
                "completion_fraction": checkin.get("completion_fraction"),
                "outcome": checkin["outcome"],
            }
        ]

    def milestone_checkin(
        self,
        milestone_id: str,
        *,
        outcome: str,
        actual_minutes: int | None = None,
        completion_fraction: float | None = None,
        note: str | None = None,
        propose_replan: bool = True,
    ) -> dict[str, Any]:
        normalized = str(outcome or "").strip().lower()
        if normalized not in {"done", "partial", "not_started", "blocked", "skipped"}:
            raise PlannerError("invalid milestone outcome")
        milestone = self.store.get_daily_milestone(milestone_id)
        if milestone is None:
            raise PlannerError(f"daily milestone not found: {milestone_id}")
        plan = self.store.get_milestone_plan(milestone["plan_id"])
        if plan is None or plan["status"] != "confirmed":
            raise PlannerError("check-ins require a confirmed milestone plan")
        if milestone["status"] not in {"planned", "in_progress"}:
            raise PlannerError("milestone already has a terminal status")
        if normalized == "partial":
            if actual_minutes is None or int(actual_minutes) <= 0:
                raise PlannerError("partial check-in requires positive actual_minutes")
            if completion_fraction is None or not 0 < float(completion_fraction) < 1:
                raise PlannerError(
                    "partial check-in requires completion_fraction between 0 and 1"
                )
        if normalized == "blocked" and not str(note or "").strip():
            raise PlannerError("blocked check-in requires a note")
        now = utc_iso(self._now())
        try:
            checkin = self.store.save_milestone_checkin(
                milestone_id,
                outcome=normalized,
                actual_minutes=actual_minutes,
                completion_fraction=completion_fraction,
                note=str(note).strip() if note else None,
                now=now,
            )
            observations = self.store.save_estimate_observations(
                checkin=checkin,
                milestone=milestone,
                observations=self._estimate_observations(milestone, checkin),
                now=now,
            )
            milestone = self.store.set_daily_milestone_status(
                milestone_id,
                status={
                    "done": "done",
                    "partial": "partial",
                    "not_started": "missed",
                    "blocked": "blocked",
                    "skipped": "missed",
                }[normalized],
                now=now,
            )
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        self.record_event(
            event_type=f"checkin.{normalized}",
            source="milestone_checkin",
            dedupe_key=checkin["id"],
            occurred_at=now,
            payload={
                "checkin_id": checkin["id"],
                "milestone_id": milestone_id.upper(),
                "plan_id": plan["id"],
                "outcome": normalized,
                "actual_minutes": checkin.get("actual_minutes"),
                "completion_fraction": checkin.get("completion_fraction"),
                "note_present": bool(checkin.get("note")),
            },
        )
        package_ids = tuple(
            {
                item["work_package_id"]
                for item in plan["milestones"]
                if item.get("work_package_id")
            }
        )
        remaining = self.store.work_package_remaining_summary(
            work_package_ids=package_ids
        )
        deadline_risk = any(
            risk.get("severity") in {"warning", "critical"}
            for risk in plan["summary"].get("deadline_risks", [])
        )
        should_replan = propose_replan and (
            normalized in {"partial", "not_started", "blocked", "skipped"}
            or (normalized == "done" and deadline_risk)
        )
        proposed = (
            self.milestone_preview(
                start_at=self._now(), use_calendar=None, base_plan_id=plan["id"]
            )
            if should_replan
            else None
        )
        return {
            "checkin": checkin,
            "milestone": milestone,
            "estimate_observations": observations,
            "remaining": remaining,
            "proposed_replan": proposed,
        }

    def milestone_snooze(
        self, milestone_id: str, *, minutes: int
    ) -> dict[str, Any]:
        delay = int(minutes)
        if not 5 <= delay <= 24 * 60:
            raise PlannerError("snooze minutes must be between 5 and 1440")
        milestone = self.store.get_daily_milestone(milestone_id)
        if milestone is None:
            raise PlannerError(f"daily milestone not found: {milestone_id}")
        plan = self.store.get_milestone_plan(milestone["plan_id"])
        if plan is None or plan["status"] != "confirmed":
            raise PlannerError("snooze requires a confirmed milestone plan")
        if milestone["status"] not in {"planned", "in_progress"}:
            raise PlannerError("only an unfinished milestone can be snoozed")
        now = self._now()
        updated = self.store.set_daily_milestone_reminder_anchor(
            milestone_id,
            reminder_anchor={
                "kind": "snoozed_until",
                "at": utc_iso(now + timedelta(minutes=delay)),
            },
            now=utc_iso(now),
        )
        assert updated is not None
        return updated

    def milestone_overview(self) -> dict[str, Any]:
        plans = self.store.list_milestone_plans(limit=50)
        active_summary = next(
            (value for value in plans if value["status"] == "confirmed"), None
        )
        draft_summary = next(
            (value for value in plans if value["status"] == "draft"), None
        )
        active = (
            self.store.get_milestone_plan(active_summary["id"])
            if active_summary
            else None
        )
        draft = (
            self.store.get_milestone_plan(draft_summary["id"])
            if draft_summary
            else None
        )
        local_today = (
            self._now()
            .astimezone(resolve_timezone(self.settings.timezone))
            .date()
            .isoformat()
        )
        today = (
            self.store.list_daily_milestones(
                plan_id=active["id"],
                milestone_date=local_today,
                statuses=("planned", "in_progress", "partial", "blocked"),
            )
            if active
            else []
        )
        packages = [
            value
            for value in self.store.list_work_packages(limit=200)
            if value["status"] in {"proposed", "active"}
        ]
        package_ids = tuple(value["id"] for value in packages)
        return {
            "milestone_plan": active,
            "milestone_draft": draft,
            "today_milestones": today,
            "work_packages": packages,
            "remaining": (
                self.store.work_package_remaining_summary(
                    work_package_ids=package_ids
                )
                if package_ids
                else {"p50_minutes": 0, "p90_minutes": 0}
            ),
            "pending_events": self.store.list_planner_events(
                status="pending", limit=20
            ),
            "recent_agent_runs": self.store.list_agent_runs(limit=20),
        }

    def overview(self) -> dict[str, Any]:
        """Return one bounded, read-only snapshot for conversational UIs.

        The snapshot deliberately composes existing Planner reads instead of
        introducing a desktop-specific state path.  Desktop, messaging, and
        future plugin surfaces can all consume the same contract through the
        normal ``/plan`` command registration.
        """
        items = self.list(limit=200)
        tasks = self.list_tasks(limit=200)
        visible_goals = self.list_goals(status="active", limit=100)
        visible_goals.extend(self.list_goals(status="paused", limit=100))
        schedules = self.list_schedules(limit=20)
        active_schedule_summary = next(
            (schedule for schedule in schedules if schedule["status"] == "confirmed"),
            None,
        )
        draft_schedule_summary = next(
            (schedule for schedule in schedules if schedule["status"] == "draft"),
            None,
        )
        milestone_state = self.milestone_overview()

        return {
            "schema_version": 2,
            "generated_at": utc_iso(self._now()),
            "status": self.status(),
            "today": self.today(),
            "items": items,
            "goals": visible_goals,
            "tasks": tasks,
            "availability": self.list_availability(),
            "profile_facts": self.list_profile_facts(),
            "monitor_proposals": self.list_monitor_proposals(),
            "sources": self.list_ingest_sources(),
            "active_schedule": (
                self.get_schedule(active_schedule_summary["id"])
                if active_schedule_summary
                else None
            ),
            "draft_schedule": (
                self.get_schedule(draft_schedule_summary["id"])
                if draft_schedule_summary
                else None
            ),
            "recent_schedules": schedules,
            "milestone_plan": milestone_state["milestone_plan"],
            "milestone_draft": milestone_state["milestone_draft"],
            "today_milestones": milestone_state["today_milestones"],
            "work_packages": milestone_state["work_packages"],
            "pending_events": milestone_state["pending_events"],
            "remaining": milestone_state["remaining"],
            "recent_agent_runs": milestone_state["recent_agent_runs"],
        }

    def dispatch_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        cron_job_state: dict[str, Any] | None = None,
        delivery_target: str = "local",
    ) -> str:
        current = now or self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        state = dict(cron_job_state or {})
        last_run_at = parse_datetime(state.get("last_run_at"), "UTC")
        if last_run_at is not None:
            error = state.get("last_delivery_error") or state.get("last_error")
            for batch in self.store.list_pending_milestone_reminder_batches():
                emitted_at = parse_datetime(batch["emitted_at"], "UTC")
                if emitted_at is not None and last_run_at > emitted_at:
                    self.store.reconcile_milestone_reminder_batch(
                        batch_id=batch["batch_id"],
                        delivered=not bool(error),
                        error=str(error) if error else None,
                        reconciled_at=utc_iso(current),
                    )

        message_parts: list[str] = []
        rows = self.store.claim_due_nudges(now=utc_iso(current), limit=limit)
        for row in rows:
            tz_name = row["timezone"]
            lines = [f"[{row['item_id']}] {row['title']}"]
            if row.get("due_at"):
                lines.append(f"截止：{local_display(row['due_at'], tz_name)}")
            if row.get("event_at"):
                lines.append(f"活动：{local_display(row['event_at'], tz_name)}")
            if row.get("ack_required"):
                lines.append(
                    f"请回复 /plan ack {row['item_id']}，完成后回复 "
                    f"/plan done {row['item_id']}。"
                )
            else:
                lines.append(f"完成后回复 /plan done {row['item_id']}。")
            message_parts.append("\n".join(lines))

        plans = self.store.list_milestone_plans(status="confirmed", limit=1)
        active_plan = (
            self.store.get_milestone_plan(plans[0]["id"]) if plans else None
        )
        if active_plan is not None:
            milestones = self.store.list_daily_milestones(
                plan_id=active_plan["id"],
                statuses=("planned", "in_progress"),
            )
            groups = due_milestone_reminders(
                milestones=milestones,
                fixed_commitments=self._milestone_fixed_commitments(
                    window_start=current,
                    window_end=current + timedelta(days=1),
                    external_events=[],
                ),
                sent=self.store.list_milestone_reminder_deliveries(
                    milestone_ids=tuple(value["id"] for value in milestones)
                ),
                now=current,
                merge_minutes=self.settings.reminder_merge_minutes,
                recovery_minutes=self.settings.reminder_recovery_minutes,
            )
            ready = [group for group in groups if group["ready"]]
            candidates = [
                {
                    "milestone_id": milestone["id"],
                    "dedupe_key": dedupe_key,
                    "scheduled_for": group["scheduled_for"],
                }
                for group in ready
                for milestone, dedupe_key in zip(
                    group["milestones"], group["dedupe_keys"], strict=True
                )
            ]
            if candidates:
                emitted_at = utc_iso(current)
                batch_material = json.dumps(
                    {
                        "emitted_at": emitted_at,
                        "dedupe_keys": sorted(
                            value["dedupe_key"] for value in candidates
                        ),
                    },
                    sort_keys=True,
                )
                batch_id = "MRB-" + hashlib.sha256(
                    batch_material.encode("utf-8")
                ).hexdigest()[:12].upper()
                claimed = self.store.claim_milestone_reminder_batch(
                    batch_id=batch_id,
                    reminders=candidates,
                    emitted_at=emitted_at,
                    delivery_target=delivery_target,
                )
                claimed_keys = {value["dedupe_key"] for value in claimed}
                local_timezone = resolve_timezone(self.settings.timezone)
                local_today = current.astimezone(local_timezone).date()
                risk_order = {"normal": 0, "warning": 1, "critical": 2}
                risk = max(
                    (
                        str(value.get("severity") or "normal")
                        for value in active_plan["summary"].get(
                            "deadline_risks", []
                        )
                    ),
                    key=lambda value: risk_order.get(value, 0),
                    default="normal",
                )
                for group in ready:
                    for milestone, dedupe_key in zip(
                        group["milestones"], group["dedupe_keys"], strict=True
                    ):
                        if dedupe_key not in claimed_keys:
                            continue
                        package = self.store.get_work_package(
                            milestone["work_package_id"]
                        )
                        if package is None:
                            continue
                        remaining = self.store.work_package_remaining_summary(
                            work_package_ids=(package["id"],)
                        )
                        internal = parse_datetime(
                            package.get("recommended_internal_deadline")
                            or package["hard_deadline"],
                            self.settings.timezone,
                        )
                        hard = parse_datetime(
                            package["hard_deadline"], self.settings.timezone
                        )
                        assert internal is not None and hard is not None
                        message_parts.append(
                            render_milestone_reminder(
                                milestone,
                                summary={
                                    "p50_remaining_minutes": remaining[
                                        "p50_minutes"
                                    ],
                                    "p90_remaining_minutes": remaining[
                                        "p90_minutes"
                                    ],
                                    "days_to_internal_deadline": max(
                                        0,
                                        (
                                            internal.astimezone(
                                                local_timezone
                                            ).date()
                                            - local_today
                                        ).days,
                                    ),
                                    "days_to_hard_deadline": max(
                                        0,
                                        (
                                            hard.astimezone(local_timezone).date()
                                            - local_today
                                        ).days,
                                    ),
                                    "risk": risk,
                                },
                            )
                        )

        if not message_parts:
            return ""
        return "⏰ Personal Planner\n\n" + "\n\n".join(message_parts)

    def status(self) -> dict[str, Any]:
        return {
            "database": str(Path(self.store.path)),
            "timezone": self.settings.timezone,
            "counts": self.store.counts(),
        }


def _severity_rank(value: str) -> int:
    return {"normal": 0, "high": 1, "urgent": 2}.get(value, 0)


def _payload_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _calendar_snapshot_hash(
    events: list[dict[str, Any]],
    *,
    calendar_ids: tuple[str, ...] | list[str],
) -> str:
    material = {
        "calendar_ids": sorted(str(value) for value in calendar_ids),
        "events": sorted(
            (
                {
                    "calendar_id": str(event.get("calendar_id") or ""),
                    "event_id": str(event.get("event_id") or ""),
                    "starts_at": str(event.get("starts_at") or ""),
                    "ends_at": str(event.get("ends_at") or ""),
                    "status": str(event.get("status") or ""),
                    "all_day": bool(event.get("all_day")),
                }
                for event in events
            ),
            key=lambda event: (
                event["calendar_id"],
                event["event_id"],
                event["starts_at"],
                event["ends_at"],
            ),
        ),
    }
    return _payload_hash(material)


def _plan_calendar_preview_hash(preview: dict[str, Any]) -> str:
    material = {
        "plan_id": preview["plan_id"],
        "provider": preview["provider"],
        "calendar_id": preview["calendar_id"],
        "events": [
            {
                key: event.get(key)
                for key in (
                    "block_id",
                    "task_id",
                    "summary",
                    "start",
                    "end",
                    "description",
                    "location",
                    "payload_hash",
                )
            }
            for event in preview["events"]
        ],
    }
    return _payload_hash(material)


def _normalize_choice(value: Any, choices: set[str], *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in choices:
        raise PlannerError(
            f"{field} must be one of {', '.join(sorted(choices))}"
        )
    return normalized


def _profile_identifier(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "_")
    if not normalized:
        raise PlannerError(f"{field} is required")
    if len(normalized) > 80:
        raise PlannerError(f"{field} must be at most 80 characters")
    if not all(
        character.isalnum() or character in {"_", "-", "."}
        for character in normalized
    ):
        raise PlannerError(
            f"{field} may contain only letters, numbers, _, -, or ."
        )
    return normalized


def _bounded_minutes(
    value: Any, *, default: int, field: str, maximum: int
) -> int:
    try:
        parsed = default if value is None else int(value)
    except (TypeError, ValueError) as exc:
        raise PlannerError(f"{field} must be an integer") from exc
    if not 5 <= parsed <= maximum:
        raise PlannerError(f"{field} must be between 5 and {maximum}")
    return parsed


def _parse_weekday(value: str | int) -> int:
    if isinstance(value, int) or str(value).strip().isdigit():
        parsed = int(value)
        if 0 <= parsed <= 6:
            return parsed
    normalized = str(value or "").strip().lower()
    if normalized in WEEKDAYS:
        return WEEKDAYS[normalized]
    raise PlannerError("weekday must be 0-6 or mon-sun")


def _weekday_name(value: int) -> str:
    return ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[
        value
    ]


def _parse_clock(value: str, *, allow_24: bool = False) -> int:
    raw = str(value or "").strip()
    if allow_24 and raw == "24:00":
        return 24 * 60
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError) as exc:
        raise PlannerError("time must use HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise PlannerError("time must use HH:MM")
    return hour * 60 + minute


def _format_clock(value: int) -> str:
    if value == 24 * 60:
        return "24:00"
    return f"{value // 60:02d}:{value % 60:02d}"
