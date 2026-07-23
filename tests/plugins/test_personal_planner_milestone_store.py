from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.personal_planner.store import PlannerStore


NOW = "2026-07-21T09:00:00+08:00"


def work_package_fixture() -> dict:
    return {
        "domain": "research",
        "objective": "Complete experiment 1",
        "source_event_ids": ["EV-1"],
        "definition_of_done": ["Run completed", "Results recorded"],
        "hard_deadline": "2026-07-31T18:00:00+08:00",
        "recommended_internal_deadline": "2026-07-29T18:00:00+08:00",
        "effort": {
            "p50_minutes": 210,
            "p90_minutes": 330,
            "confidence": 0.6,
            "basis": "Initial domain estimate",
        },
        "steps": [
            {
                "local_id": "prepare",
                "title": "Prepare environment",
                "p50_minutes": 90,
                "p90_minutes": 120,
                "depends_on": [],
                "energy": "high",
                "splittable": True,
            },
            {
                "local_id": "run",
                "title": "Run experiment",
                "p50_minutes": 120,
                "p90_minutes": 210,
                "depends_on": ["prepare"],
                "energy": "high",
                "splittable": False,
            },
        ],
        "risks": [
            {"kind": "rerun", "probability": "medium", "buffer_minutes": 120}
        ],
        "assumptions": ["Experiments are independent"],
        "questions": ["Does completion require a report?"],
    }


class MilestonePlannerStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PlannerStore(Path(self.temp.name) / "planner.db")

    def tearDown(self):
        self.temp.cleanup()

    def _milestone(self, package_id: str, suffix: str = "prepare") -> dict:
        return {
            "allocation_key": f"{package_id}:{suffix}:2026-07-22",
            "date": "2026-07-22",
            "window_end": "2026-07-22T22:00:00+08:00",
            "domain": "research",
            "work_package_id": package_id,
            "title": "Prepare the experiment environment",
            "target_minutes": 90,
            "definition_of_done": ["Environment starts successfully"],
            "source_step_ids": [suffix],
            "reminder_anchor": {"time": "09:00", "kind": "daily_target"},
            "explanation": {"why_today": "Leaves recovery time before deadline"},
        }

    def _draft(self, package_id: str, *, summary: dict | None = None) -> dict:
        return self.store.create_milestone_plan_draft(
            window_start="2026-07-21",
            window_end="2026-07-31",
            timezone="Asia/Shanghai",
            summary=summary or {"strategy": "finish before the internal deadline"},
            milestones=[self._milestone(package_id)],
            now=NOW,
        )

    def test_planner_event_dedupe_returns_existing_event(self):
        first = self.store.create_planner_event(
            event_type="notification.detected",
            source="gmail",
            dedupe_key="gmail:message-1",
            payload={"subject": "CET-6 registration opens soon"},
            occurred_at=NOW,
        )
        repeated = self.store.create_planner_event(
            event_type="notification.detected",
            source="gmail",
            dedupe_key="gmail:message-1",
            payload={"subject": "must not overwrite the original event"},
            occurred_at="2026-07-21T09:05:00+08:00",
        )

        self.assertEqual(repeated["id"], first["id"])
        self.assertEqual(repeated["payload"], first["payload"])
        self.assertEqual(len(self.store.list_planner_events()), 1)

    def test_work_package_round_trip_preserves_validated_contract(self):
        created = self.store.save_work_package(work_package_fixture(), now=NOW)
        loaded = self.store.get_work_package(created["id"])

        self.assertEqual(created["status"], "proposed")
        self.assertEqual(loaded["objective"], "Complete experiment 1")
        self.assertEqual(loaded["steps"][1]["depends_on"], ["prepare"])
        self.assertEqual(loaded["effort"]["p90_minutes"], 330)
        self.assertEqual(
            [value["id"] for value in self.store.list_work_packages()],
            [created["id"]],
        )

    def test_confirming_draft_activates_package_and_milestones_atomically(self):
        package = self.store.save_work_package(work_package_fixture(), now=NOW)
        draft = self._draft(package["id"])

        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["milestones"][0]["status"], "draft")
        confirmed = self.store.confirm_milestone_plan(draft["id"], now=NOW)

        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["milestones"][0]["status"], "planned")
        self.assertEqual(
            self.store.get_work_package(package["id"])["status"], "active"
        )

    def test_replacement_requires_the_explicit_confirmed_base_plan(self):
        package = self.store.save_work_package(work_package_fixture(), now=NOW)
        base = self.store.confirm_milestone_plan(
            self._draft(package["id"])["id"], now=NOW
        )
        replacement = self._draft(
            package["id"], summary={"base_plan_id": base["id"], "reason": "progress"}
        )

        with self.assertRaisesRegex(ValueError, "replace_plan_id"):
            self.store.confirm_milestone_plan(replacement["id"], now=NOW)

        confirmed = self.store.confirm_milestone_plan(
            replacement["id"], now=NOW, replace_plan_id=base["id"]
        )

        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(
            self.store.get_milestone_plan(base["id"])["status"], "superseded"
        )
        self.assertEqual(
            self.store.get_milestone_plan(base["id"])["milestones"][0]["status"],
            "superseded",
        )

    def test_expert_result_and_agent_run_preserve_the_decision_audit_trace(self):
        event = self.store.create_planner_event(
            event_type="notification.detected",
            source="gmail",
            dedupe_key="gmail:message-2",
            payload={"subject": "Research experiment deadline"},
            occurred_at=NOW,
        )
        expert = self.store.save_expert_result(
            event_id=event["id"],
            domain="research",
            result={"work_packages": [work_package_fixture()]},
            provider="openai",
            model="planner-test-model",
            usage={"input_tokens": 120, "output_tokens": 80},
            now=NOW,
        )
        package = self.store.save_work_package(work_package_fixture(), now=NOW)
        draft = self._draft(package["id"])
        run = self.store.save_agent_run(
            trigger_event_id=event["id"],
            selected_experts=["research"],
            expert_result_ids=[expert["id"]],
            decision={"action": "propose_plan", "reason": "deadline detected"},
            draft_plan_id=draft["id"],
            approval_status="pending",
            outcome="draft_created",
            reasoning_summary="Research work needs staged progress.",
            usage={"input_tokens": 300, "output_tokens": 140},
            now=NOW,
        )

        self.assertEqual(expert["result"]["work_packages"][0]["domain"], "research")
        self.assertEqual(expert["usage"]["output_tokens"], 80)
        self.assertEqual(
            self.store.list_expert_results(event_id=event["id"])[0]["id"],
            expert["id"],
        )
        loaded_run = self.store.get_agent_run(run["id"])
        self.assertEqual(loaded_run["selected_experts"], ["research"])
        self.assertEqual(loaded_run["expert_result_ids"], [expert["id"]])
        self.assertEqual(loaded_run["decision"]["action"], "propose_plan")
        self.assertEqual(loaded_run["usage"]["input_tokens"], 300)

    def test_milestone_checkin_is_idempotent_and_updates_milestone_state(self):
        package = self.store.save_work_package(work_package_fixture(), now=NOW)
        plan = self.store.confirm_milestone_plan(
            self._draft(package["id"])["id"], now=NOW
        )
        milestone_id = plan["milestones"][0]["id"]
        first = self.store.save_milestone_checkin(
            milestone_id=milestone_id,
            outcome="partial",
            actual_minutes=45,
            completion_fraction=0.5,
            note="Environment installs, verification remains.",
            now=NOW,
        )
        revised = self.store.save_milestone_checkin(
            milestone_id=milestone_id,
            outcome="done",
            actual_minutes=80,
            completion_fraction=1.0,
            note="Verified.",
            now="2026-07-21T18:00:00+08:00",
        )

        self.assertEqual(revised["id"], first["id"])
        self.assertEqual(revised["outcome"], "done")
        self.assertEqual(
            self.store.get_milestone_plan(plan["id"])["milestones"][0]["status"],
            "done",
        )

    def test_rejecting_a_draft_cancels_milestones_without_activating_package(self):
        package = self.store.save_work_package(work_package_fixture(), now=NOW)
        draft = self._draft(package["id"])

        rejected = self.store.reject_milestone_plan(draft["id"], now=NOW)

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["milestones"][0]["status"], "cancelled")
        self.assertEqual(
            self.store.get_work_package(package["id"])["status"], "proposed"
        )


if __name__ == "__main__":
    unittest.main()
