from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugins.personal_planner.config import PlannerSettings
from plugins.personal_planner.milestone_reminders import (
    due_milestone_reminders,
    render_milestone_reminder,
)
from plugins.personal_planner.service import PlannerError, PlannerService
from plugins.personal_planner.store import PlannerStore
from plugins.personal_planner.timeutil import parse_datetime, utc_iso


NOW = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)


def work_package_fixture(*, event_id: str = "EV-1") -> dict:
    return {
        "domain": "research",
        "objective": "Run experiment",
        "source_event_ids": [event_id],
        "definition_of_done": ["Result recorded"],
        "hard_deadline": "2026-07-31T18:00:00+08:00",
        "recommended_internal_deadline": "2026-07-29T18:00:00+08:00",
        "effort": {
            "p50_minutes": 120,
            "p90_minutes": 180,
            "confidence": 0.6,
            "basis": "Fixture estimate",
        },
        "steps": [
            {
                "local_id": "run",
                "title": "Run experiment",
                "p50_minutes": 120,
                "p90_minutes": 180,
                "depends_on": [],
                "energy": "high",
                "splittable": True,
            }
        ],
        "risks": [],
        "assumptions": [],
        "questions": [],
    }


class FakeExpertRunner:
    def __init__(self):
        self.calls = []

    def analyze(self, domain: str, *, event: dict, snapshot: dict) -> dict:
        self.calls.append({"domain": domain, "event": event, "snapshot": snapshot})
        return {
            "package": work_package_fixture(event_id=event["id"]),
            "provider": "test",
            "model": "test-model",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "cost_usd": 0.01,
            },
        }


class MilestonePlannerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PlannerStore(Path(self.temp.name) / "planner.db")
        self.service = PlannerService(
            store=self.store,
            settings=PlannerSettings(calendar_busy_read=False),
            now_fn=lambda: NOW,
        )
        for weekday in range(7):
            self.service.add_availability(
                weekday=weekday,
                start_time="08:00",
                end_time="20:00",
                energy="medium",
            )

    def tearDown(self):
        self.temp.cleanup()

    def _draft(self) -> dict:
        self.service.save_work_package(work_package_fixture())
        return self.service.milestone_preview(
            start_at="2026-07-20T00:00:00Z",
            days=12,
            use_calendar=False,
        )

    def test_event_analysis_atomically_persists_auditable_agent_outputs(self):
        event = self.service.record_event(
            event_type="work.requested",
            source="conversation",
            payload={"text": "7月31日前完成实验"},
            dedupe_key="conversation:turn-1",
        )
        runner = FakeExpertRunner()

        result = self.service.analyze_event(
            event["id"], domains=["research"], expert_runner=runner
        )

        self.assertEqual(result["event"]["status"], "processed")
        self.assertEqual(result["work_packages"][0]["status"], "proposed")
        self.assertEqual(result["expert_results"][0]["domain"], "research")
        self.assertEqual(result["agent_run"]["approval_status"], "pending")
        self.assertEqual(result["agent_run"]["usage"]["total_tokens"], 150)
        self.assertEqual(len(runner.calls), 1)

    def test_capture_bridges_changed_notices_into_the_agent_event_queue(self):
        created = self.service.capture(
            raw_content="Registration closes July 31",
            title="Registration notice",
            due_at="2026-07-31T18:00:00+08:00",
            source_type="conversation",
            source_uri="conversation://turn-1",
        )
        self.service.capture(
            raw_content="Registration closes July 31",
            title="Registration notice",
            due_at="2026-07-31T18:00:00+08:00",
            source_type="conversation",
            source_uri="conversation://turn-1",
        )
        updated = self.service.capture(
            raw_content="Registration closes July 30",
            title="Registration notice",
            due_at="2026-07-30T18:00:00+08:00",
            source_type="conversation",
            source_uri="conversation://turn-1",
        )

        events = self.store.list_planner_events(status="pending")
        self.assertEqual([event["event_type"] for event in events], [
            "notice.changed",
            "notice.changed",
        ])
        self.assertEqual({event["payload"]["item_id"] for event in events}, {created["id"]})
        self.assertEqual(
            {event["payload"]["revision_number"] for event in events}, {1, 2}
        )
        self.assertEqual(created["id"], updated["id"])
        self.assertNotIn("raw_content", events[0]["payload"])

        radar = self.service.capture(
            raw_content="Official internship application opened",
            title="Internship radar finding",
            due_at="2026-08-10T18:00:00+08:00",
            source_type="web_radar",
            source_uri="https://example.edu/jobs/1",
        )
        radar_event = next(
            event
            for event in self.store.list_planner_events(status="pending")
            if event["event_type"] == "radar.finding_changed"
        )
        self.assertEqual(radar_event["event_type"], "radar.finding_changed")
        self.assertEqual(radar_event["payload"]["content_hash"], radar["content_hash"])

    def test_partial_checkin_reduces_work_and_creates_replan_draft(self):
        plan = self._draft()
        confirmed = self.service.confirm_milestone_plan(plan["id"], confirmed=True)
        milestone = confirmed["milestones"][0]

        result = self.service.milestone_checkin(
            milestone["id"],
            outcome="partial",
            actual_minutes=20,
            completion_fraction=0.5,
            note="Half of this target is complete",
            propose_replan=True,
        )

        self.assertEqual(result["checkin"]["outcome"], "partial")
        self.assertEqual(len(result["estimate_observations"]), 1)
        self.assertIsNotNone(result["proposed_replan"])
        self.assertEqual(
            result["proposed_replan"]["summary"]["base_plan_id"], plan["id"]
        )
        self.assertEqual(
            self.store.get_milestone_plan(plan["id"])["status"], "confirmed"
        )
        self.assertGreater(result["remaining"]["p50_minutes"], 0)
        checkin_event = next(
            event
            for event in self.store.list_planner_events(status="pending")
            if event["event_type"] == "checkin.partial"
        )
        self.assertEqual(
            checkin_event["payload"]["milestone_id"], milestone["id"]
        )

    def test_confirmation_replacement_and_rejection_require_explicit_decisions(self):
        plan = self._draft()
        with self.assertRaisesRegex(PlannerError, "explicit approval"):
            self.service.confirm_milestone_plan(plan["id"], confirmed=False)

        confirmed = self.service.confirm_milestone_plan(plan["id"], confirmed=True)
        replacement = self.service.milestone_preview(
            days=7, use_calendar=False, base_plan_id=confirmed["id"]
        )
        with self.assertRaisesRegex(PlannerError, "replace_plan_id"):
            self.service.confirm_milestone_plan(replacement["id"], confirmed=True)

        rejected = self.service.reject_milestone_plan(replacement["id"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(
            self.store.get_milestone_plan(confirmed["id"])["status"], "confirmed"
        )

    def test_snooze_and_overview_expose_only_current_milestone_state(self):
        confirmed = self.service.confirm_milestone_plan(
            self._draft()["id"], confirmed=True
        )
        milestone = confirmed["milestones"][0]

        snoozed = self.service.milestone_snooze(milestone["id"], minutes=45)
        overview = self.service.milestone_overview()

        self.assertEqual(snoozed["reminder_anchor"]["kind"], "snoozed_until")
        self.assertEqual(overview["milestone_plan"]["id"], confirmed["id"])
        self.assertIn("remaining", overview)
        self.assertEqual(overview["pending_events"], [])

    def test_action_reminder_reports_today_and_remaining_workload(self):
        message = render_milestone_reminder(
            {
                "id": "DM-1",
                "title": "Tailor internship A resume",
                "target_minutes": 60,
                "definition_of_done": ["First tailored draft is complete"],
            },
            summary={
                "p50_remaining_minutes": 420,
                "p90_remaining_minutes": 540,
                "days_to_internal_deadline": 3,
                "days_to_hard_deadline": 4,
                "risk": "normal",
            },
        )
        self.assertIn("60", message)
        self.assertIn("420", message)
        self.assertIn("3", message)
        self.assertIn("done", message.lower())
        self.assertIn("partial", message.lower())

    def test_reminder_is_deferred_during_fixed_commitment_and_coalesced(self):
        due = due_milestone_reminders(
            milestones=[
                {
                    "id": "DM-1",
                    "reminder_anchor": {
                        "kind": "first_suitable_window",
                        "recommended_at": "2026-07-21T10:00:00Z",
                    },
                    "status": "planned",
                }
            ],
            fixed_commitments=[
                {
                    "starts_at": "2026-07-21T09:00:00Z",
                    "ends_at": "2026-07-21T11:00:00Z",
                }
            ],
            sent=[],
            now=parse_datetime("2026-07-21T10:05:00Z", "UTC"),
            merge_minutes=60,
            recovery_minutes=30,
        )
        self.assertEqual(due[0]["deliver_at"], "2026-07-21T11:30:00Z")
        self.assertFalse(due[0]["ready"])

    def test_dispatch_uses_two_phase_milestone_delivery_receipts(self):
        confirmed = self.service.confirm_milestone_plan(
            self._draft()["id"], confirmed=True
        )
        milestone = confirmed["milestones"][0]

        message = self.service.dispatch_due(
            now=NOW,
            cron_job_state=None,
            delivery_target="local",
        )

        self.assertIn(milestone["title"], message)
        pending = self.store.list_pending_milestone_reminder_batches()
        self.assertEqual(len(pending), 1)
        batch_id = pending[0]["batch_id"]
        self.assertEqual(pending[0]["status"], "pending")

        self.service.dispatch_due(
            now=NOW + timedelta(minutes=1),
            cron_job_state={
                "last_run_at": utc_iso(NOW + timedelta(minutes=1)),
                "last_error": None,
                "last_delivery_error": None,
            },
            delivery_target="local",
        )
        delivered = self.store.list_milestone_reminder_deliveries(
            milestone_ids=(milestone["id"],)
        )
        self.assertEqual(delivered[0]["batch_id"], batch_id)
        self.assertEqual(delivered[0]["status"], "sent")

        retry_batch = "MRB-RETRY"
        self.store.claim_milestone_reminder_batch(
            batch_id=retry_batch,
            reminders=[
                {
                    "milestone_id": milestone["id"],
                    "dedupe_key": f"{milestone['id']}:retry",
                    "scheduled_for": utc_iso(NOW),
                }
            ],
            emitted_at=utc_iso(NOW),
            delivery_target="local",
        )
        self.store.reconcile_milestone_reminder_batch(
            batch_id=retry_batch,
            delivered=False,
            error="delivery failed",
            reconciled_at=utc_iso(NOW + timedelta(minutes=1)),
        )
        retryable = self.store.list_milestone_reminder_deliveries(
            milestone_ids=(milestone["id"],)
        )
        retry_row = next(row for row in retryable if row["batch_id"] == retry_batch)
        self.assertEqual(retry_row["status"], "retryable")


if __name__ == "__main__":
    unittest.main()
