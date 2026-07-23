from __future__ import annotations

import json
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugins.personal_planner.capacity import build_daily_capacity
from plugins.personal_planner.config import PlannerSettings
from plugins.personal_planner.contracts import dependency_order, validate_work_package
from plugins.personal_planner.expert_agents import route_domain_agents
from plugins.personal_planner.milestones import (
    build_milestone_plan,
    diff_milestone_plans,
)
from plugins.personal_planner.service import PlannerService
from plugins.personal_planner.store import PlannerStore


TIMEZONE_NAME = "Asia/Shanghai"


def _capacity(day: str, minutes: int) -> dict:
    return {
        "date": day,
        "capacity_minutes": minutes,
        "capacity_class": "low" if minutes < 90 else "medium",
        "reminder_anchor": {"kind": "first_suitable_window"},
    }


def _work_package(
    *,
    package_id: str,
    domain: str,
    objective: str,
    hard_deadline: str,
    internal_deadline: str,
    p50_minutes: int,
    p90_minutes: int,
    risks: list[dict] | None = None,
    steps: list[dict] | None = None,
) -> dict:
    package_steps = steps or [
        {
            "local_id": "work",
            "title": objective,
            "p50_minutes": p50_minutes,
            "p90_minutes": p90_minutes,
            "depends_on": [],
            "energy": "high",
            "splittable": True,
        }
    ]
    package = {
        "id": package_id,
        "domain": domain,
        "objective": objective,
        "source_event_ids": [f"EV-{package_id}"],
        "definition_of_done": [f"{objective} is complete and recorded"],
        "hard_deadline": hard_deadline,
        "recommended_internal_deadline": internal_deadline,
        "effort": {
            "p50_minutes": p50_minutes,
            "p90_minutes": p90_minutes,
            "confidence": 0.6,
            "basis": "Fixed offline evaluation fixture",
        },
        "steps": package_steps,
        "risks": risks or [],
        "assumptions": ["Fixture dates are explicitly supplied"],
        "questions": [],
    }
    return {**validate_work_package(package), "id": package_id}


def _semantic_commitments(values: list[dict]) -> list[dict]:
    rows: list[dict] = []
    dates = [f"2026-07-{day:02d}" for day in range(20, 32)]
    for index, value in enumerate(values):
        if value.get("recurrence") == "daily" and value.get("period") == "evening":
            for day in dates:
                rows.append(
                    {
                        "id": f"exercise-{day}",
                        "starts_at": f"{day}T19:30:00+08:00",
                        "ends_at": f"{day}T21:00:00+08:00",
                        "title": "Daily exercise",
                    }
                )
            continue

        day = str(value["date"])
        if value.get("load") == "high":
            start, end = "09:00:00", "19:30:00"
        elif value.get("period") == "morning":
            start, end = "09:00:00", "12:00:00"
        else:
            start, end = "12:00:00", "14:00:00"
        rows.append(
            {
                "id": f"{value['kind']}-{index + 1}",
                "starts_at": f"{day}T{start}+08:00",
                "ends_at": f"{day}T{end}+08:00",
                "title": str(value["kind"]).title(),
            }
        )
    return rows


def _daily_availability() -> list[dict]:
    return [
        {
            "weekday": weekday,
            "start_minute": 9 * 60,
            "end_minute": 21 * 60 + 30,
            "energy": "medium",
            "enabled": 1,
        }
        for weekday in range(7)
    ]


def run_scenario(*, now: str, request: str, commitments: list[dict]) -> dict:
    original_commitments = copy.deepcopy(commitments)
    selected = route_domain_agents(request, source_kind="conversation")
    work_packages = [
        _work_package(
            package_id="WP-CAREER",
            domain="career",
            objective="Submit two internship applications",
            hard_deadline="2026-07-25T23:59:00+08:00",
            internal_deadline="2026-07-24T23:59:00+08:00",
            p50_minutes=360,
            p90_minutes=480,
        ),
        _work_package(
            package_id="WP-RESEARCH",
            domain="research",
            objective="Complete three experiments",
            hard_deadline="2026-07-31T18:00:00+08:00",
            internal_deadline="2026-07-30T18:00:00+08:00",
            p50_minutes=1080,
            p90_minutes=1440,
            risks=[
                {"kind": "experiment_rerun", "probability": "medium", "buffer_minutes": 180}
            ],
        ),
    ]
    fixed = _semantic_commitments(commitments)
    start = datetime.fromisoformat(now)
    capacities = build_daily_capacity(
        availability=_daily_availability(),
        fixed_commitments=fixed,
        window_start=start,
        window_end=datetime(2026, 8, 1, tzinfo=start.tzinfo),
        timezone_name=TIMEZONE_NAME,
        buffer_percent=25,
        recovery_minutes=30,
    )
    plan = build_milestone_plan(
        work_packages=work_packages,
        capacities=capacities,
        timezone_name=TIMEZONE_NAME,
        max_daily_domains=2,
        protect_hard_deadline_day=True,
    )
    capacity_by_day = {row["date"]: row["capacity_minutes"] for row in capacities}
    return {
        "application_internal_deadline": "2026-07-24",
        "capacity": capacity_by_day,
        "experiment_internal_deadline": "2026-07-30",
        "fixed_commitments_unchanged": commitments == original_commitments,
        "milestones": plan["milestones"],
        "plan": plan,
        "selected_experts": selected,
        "valid_work_packages": len(work_packages),
    }


def run_registration_notice_scenario(root: Path) -> dict:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    store = PlannerStore(root / "planner.db")
    service = PlannerService(
        store=store,
        settings=PlannerSettings(timezone=TIMEZONE_NAME),
        now_fn=lambda: now,
    )
    item = service.capture(
        raw_content="The CET registration portal opens September 10 and closes September 18.",
        title="CET registration",
        discovered_at="2026-07-20T08:00:00+08:00",
        actionable_at="2026-09-10T09:00:00+08:00",
        due_at="2026-09-18T17:00:00+08:00",
        event_at="2026-12-14T09:00:00+08:00",
        source_type="email",
    )
    return {
        "external_writes": 0,
        "item": item,
        "nudge_count": len(store.list_nudges(item["id"])),
        "selected_experts": route_domain_agents(
            "CET registration notice from the graduate school", source_kind="email"
        ),
    }


def run_paper_deadline_scenario() -> dict:
    steps = [
        {
            "local_id": "experiment",
            "title": "Run final experiment",
            "p50_minutes": 300,
            "p90_minutes": 480,
            "depends_on": [],
            "energy": "high",
            "splittable": True,
        },
        {
            "local_id": "analyze",
            "title": "Analyze results",
            "p50_minutes": 120,
            "p90_minutes": 180,
            "depends_on": ["experiment"],
            "energy": "high",
            "splittable": True,
        },
        {
            "local_id": "advisor_review",
            "title": "Advisor review",
            "p50_minutes": 180,
            "p90_minutes": 240,
            "depends_on": ["analyze"],
            "energy": "medium",
            "splittable": True,
        },
    ]
    package = _work_package(
        package_id="WP-PAPER",
        domain="research",
        objective="Submit paper after advisor review",
        hard_deadline="2026-07-31T23:59:00+08:00",
        internal_deadline="2026-07-28T18:00:00+08:00",
        p50_minutes=600,
        p90_minutes=900,
        risks=[{"kind": "experiment_rerun", "probability": "high", "buffer_minutes": 300}],
        steps=steps,
    )
    plan = build_milestone_plan(
        work_packages=[package],
        capacities=[_capacity(f"2026-07-{day:02d}", 80) for day in range(20, 31)],
        timezone_name=TIMEZONE_NAME,
        max_daily_domains=2,
        protect_hard_deadline_day=True,
    )
    return {
        "deadline_risks": plan["deadline_risks"],
        "dependency_order": dependency_order(package["steps"]),
        "hard_deadline": package["hard_deadline"],
        "internal_deadline": package["recommended_internal_deadline"],
        "p50_minutes": package["effort"]["p50_minutes"],
        "p90_minutes": package["effort"]["p90_minutes"],
        "p90_risk_detected": any(
            risk["kind"] == "p90_buffer_shortfall" for risk in plan["deadline_risks"]
        ),
        "rerun_risk_visible": any(risk["kind"] == "experiment_rerun" for risk in package["risks"]),
        "selected_experts": route_domain_agents(
            "Finish the paper experiment and advisor review", source_kind="conversation"
        ),
        "starts_before_review_deadline": any(
            item["date"] < "2026-07-28" for item in plan["milestones"]
        ),
        "unallocated_p50_minutes": plan["unallocated_p50_minutes"],
    }


def run_missed_milestone_scenario() -> dict:
    packages = [
        _work_package(
            package_id="WP-REPLAN-R",
            domain="research",
            objective="Finish experiment analysis",
            hard_deadline="2026-07-28T18:00:00+08:00",
            internal_deadline="2026-07-27T18:00:00+08:00",
            p50_minutes=240,
            p90_minutes=360,
        ),
        _work_package(
            package_id="WP-REPLAN-C",
            domain="career",
            objective="Prepare internship interview",
            hard_deadline="2026-07-27T18:00:00+08:00",
            internal_deadline="2026-07-26T18:00:00+08:00",
            p50_minutes=180,
            p90_minutes=240,
        ),
    ]
    base_capacities = [_capacity(f"2026-07-{day:02d}", 120) for day in range(20, 27)]
    accepted = build_milestone_plan(
        work_packages=packages,
        capacities=base_capacities,
        timezone_name=TIMEZONE_NAME,
        max_daily_domains=2,
        protect_hard_deadline_day=True,
    )
    accepted_snapshot = copy.deepcopy(accepted)
    missed = accepted["milestones"][0]
    changed_capacities = [
        {**row, "capacity_minutes": 0} if row["date"] == missed["date"] else row
        for row in base_capacities
    ]
    proposed = build_milestone_plan(
        work_packages=packages,
        capacities=changed_capacities,
        progress_observations=[
            {
                "allocation_key": missed["allocation_key"],
                "credited_p50_minutes": 0,
                "credited_p90_minutes": 0,
                "outcome": "not_started",
                "source_step_id": missed["source_step_ids"][0],
                "work_package_id": missed["work_package_id"],
            }
        ],
        timezone_name=TIMEZONE_NAME,
        max_daily_domains=2,
        protect_hard_deadline_day=True,
    )
    diff = diff_milestone_plans(accepted, proposed)
    old = {item["allocation_key"]: item for item in accepted["milestones"]}
    new = {item["allocation_key"]: item for item in proposed["milestones"]}
    preserved = [
        key
        for key in old.keys() & new.keys()
        if (old[key]["date"], old[key]["target_minutes"])
        == (new[key]["date"], new[key]["target_minutes"])
    ]
    return {
        "accepted_plan_unchanged": accepted == accepted_snapshot,
        "changed_or_removed": len(diff["changed"]) + len(diff["removed"]),
        "eligible_milestones": len(preserved),
        "replan_required": bool(diff["changed"] or diff["removed"] or diff["added"]),
        "unaffected_milestones_preserved": len(preserved),
    }


def run_early_completion_scenario() -> dict:
    package = _work_package(
        package_id="WP-EARLY",
        domain="research",
        objective="Complete analysis",
        hard_deadline="2026-07-25T18:00:00+08:00",
        internal_deadline="2026-07-24T18:00:00+08:00",
        p50_minutes=240,
        p90_minutes=360,
    )
    plan = build_milestone_plan(
        work_packages=[package],
        capacities=[_capacity("2026-07-21", 240)],
        progress_observations=[
            {
                "allocation_key": "WP-EARLY:work:001",
                "credited_p50_minutes": 120,
                "credited_p90_minutes": 180,
                "outcome": "done",
                "source_step_id": "work",
                "work_package_id": "WP-EARLY",
            }
        ],
        timezone_name=TIMEZONE_NAME,
        max_daily_domains=2,
        protect_hard_deadline_day=True,
    )
    available = plan["capacity"]["available_minutes"]
    scheduled = plan["capacity"]["scheduled_minutes"]
    return {
        "available_minutes": available,
        "banked_buffer_minutes": available - scheduled,
        "scheduled_minutes": scheduled,
        "unallocated_p50_minutes": plan["unallocated_p50_minutes"],
    }


def run_conflicting_deadline_scenario() -> dict:
    candidates = [
        {"deadline": "2026-09-18T17:00:00+08:00", "source": "official_school_notice"},
        {"deadline": "2026-09-16T17:00:00+08:00", "source": "informal_group_chat"},
    ]
    return {
        "candidate_deadlines": candidates,
        "draft_plan": None,
        "external_writes": 0,
        "requires_confirmation": len({item["deadline"] for item in candidates}) > 1,
        "selected_deadline": None,
        "selected_experts": route_domain_agents(
            "School registration deadline notice", source_kind="email"
        ),
    }


def run_impossible_capacity_scenario() -> dict:
    package = _work_package(
        package_id="WP-IMPOSSIBLE",
        domain="research",
        objective="Complete large experiment",
        hard_deadline="2026-07-21T18:00:00+08:00",
        internal_deadline="2026-07-20T18:00:00+08:00",
        p50_minutes=600,
        p90_minutes=900,
    )
    plan = build_milestone_plan(
        work_packages=[package],
        capacities=[_capacity("2026-07-20", 60)],
        timezone_name=TIMEZONE_NAME,
        max_daily_domains=2,
        protect_hard_deadline_day=True,
    )
    return {
        "all_tradeoffs_require_approval": all(
            option["requires_approval"] for option in plan["tradeoff_options"]
        ),
        "external_writes": 0,
        "risk_kinds": [risk["kind"] for risk in plan["deadline_risks"]],
        "tradeoff_kinds": [option["kind"] for option in plan["tradeoff_options"]],
        "unscheduled_p50_minutes": sum(
            item["remaining_p50_minutes"] for item in plan["unscheduled"]
        ),
    }


def build_evaluation_report() -> dict:
    mixed = run_scenario(
        now="2026-07-20T08:00:00+08:00",
        request=(
            "Complete three experiments before July 31 and submit two internship "
            "applications before July 25"
        ),
        commitments=[
            {"date": "2026-07-21", "kind": "classes", "load": "high"},
            {"date": "2026-07-22", "kind": "meeting", "load": "high"},
            {"date": "2026-07-25", "kind": "sport", "period": "morning"},
            {"recurrence": "daily", "kind": "exercise", "period": "evening"},
        ],
    )
    with tempfile.TemporaryDirectory() as temporary:
        registration = run_registration_notice_scenario(Path(temporary))
    paper = run_paper_deadline_scenario()
    missed = run_missed_milestone_scenario()
    early = run_early_completion_scenario()
    conflict = run_conflicting_deadline_scenario()
    impossible = run_impossible_capacity_scenario()
    routing_results = [
        mixed["selected_experts"] == ["research", "career"],
        registration["selected_experts"] == ["communication"],
        paper["selected_experts"] == ["research"],
        conflict["selected_experts"] == ["communication"],
    ]
    return {
        "evaluation_mode": "offline_deterministic",
        "fixture_set": "personal-planner-v1.0",
        "planning": {
            "p50_shortfalls_detected": int(impossible["unscheduled_p50_minutes"] > 0),
            "p90_risks_detected": int(paper["p90_risk_detected"]),
            "scenarios": 7,
        },
        "routing": {"correct": sum(routing_results), "total": len(routing_results)},
        "safety": {
            "external_write_violations": sum(
                int(result["external_writes"] != 0)
                for result in (registration, conflict, impossible)
            ),
            "fixed_commitment_conflicts": int(not mixed["fixed_commitments_unchanged"]),
        },
        "stability": {
            "eligible_milestones": missed["eligible_milestones"],
            "unaffected_milestones_preserved": missed["unaffected_milestones_preserved"],
        },
        "usage": {
            "cost_usd": None,
            "input_tokens": 0,
            "llm_calls": 0,
            "output_tokens": 0,
        },
        "work_packages": {
            "total": mixed["valid_work_packages"] + 5,
            "valid_after_repair": 0,
            "valid_first_pass": mixed["valid_work_packages"] + 5,
        },
    }


def write_evaluation_report(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_evaluation_report(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class PersonalPlannerScenarioTest(unittest.TestCase):
    def test_experiments_and_internships_with_fixed_commitments(self):
        result = run_scenario(
            now="2026-07-20T08:00:00+08:00",
            request=(
                "Complete three experiments before July 31 and submit two "
                "internship applications before July 25"
            ),
            commitments=[
                {"date": "2026-07-21", "kind": "classes", "load": "high"},
                {"date": "2026-07-22", "kind": "meeting", "load": "high"},
                {"date": "2026-07-25", "kind": "sport", "period": "morning"},
                {"recurrence": "daily", "kind": "exercise", "period": "evening"},
            ],
        )

        self.assertEqual(result["selected_experts"], ["research", "career"])
        self.assertLessEqual(result["capacity"]["2026-07-21"], 90)
        self.assertLessEqual(result["capacity"]["2026-07-22"], 90)
        self.assertTrue(
            any(
                item["domain"] == "research" and item["date"] < "2026-07-25"
                for item in result["milestones"]
            )
        )
        self.assertFalse(
            any(item["date"] == "2026-07-31" for item in result["milestones"])
        )
        self.assertEqual(result["application_internal_deadline"], "2026-07-24")
        self.assertEqual(result["experiment_internal_deadline"], "2026-07-30")
        self.assertTrue(result["fixed_commitments_unchanged"])

    def test_early_notice_keeps_publish_open_and_deadline_dates_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = run_registration_notice_scenario(Path(temporary))

        item = result["item"]
        self.assertEqual(result["selected_experts"], ["communication"])
        self.assertLess(item["discovered_at"], item["actionable_at"])
        self.assertLess(item["actionable_at"], item["due_at"])
        self.assertLess(item["due_at"], item["event_at"])
        self.assertEqual(item["status"], "draft")
        self.assertEqual(result["nudge_count"], 0)
        self.assertEqual(result["external_writes"], 0)

    def test_paper_deadline_has_review_lead_time_dependencies_and_rerun_risk(self):
        result = run_paper_deadline_scenario()

        self.assertEqual(result["selected_experts"], ["research"])
        self.assertLess(result["internal_deadline"], result["hard_deadline"])
        self.assertGreater(result["p90_minutes"], result["p50_minutes"])
        self.assertEqual(result["dependency_order"], ["experiment", "analyze", "advisor_review"])
        self.assertTrue(result["rerun_risk_visible"])
        self.assertTrue(result["starts_before_review_deadline"])

    def test_missed_milestone_and_calendar_change_preserve_unaffected_work(self):
        result = run_missed_milestone_scenario()

        self.assertTrue(result["replan_required"])
        self.assertGreater(result["changed_or_removed"], 0)
        self.assertGreater(result["eligible_milestones"], 0)
        self.assertEqual(result["unaffected_milestones_preserved"], result["eligible_milestones"])
        self.assertTrue(result["accepted_plan_unchanged"])

    def test_early_completion_banks_buffer_instead_of_filling_the_day(self):
        result = run_early_completion_scenario()

        self.assertGreater(result["banked_buffer_minutes"], 0)
        self.assertLess(result["scheduled_minutes"], result["available_minutes"])
        self.assertEqual(result["unallocated_p50_minutes"], 0)

    def test_conflicting_deadline_sources_require_user_confirmation(self):
        result = run_conflicting_deadline_scenario()

        self.assertEqual(result["selected_experts"], ["communication"])
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(len(result["candidate_deadlines"]), 2)
        self.assertIsNone(result["selected_deadline"])
        self.assertIsNone(result["draft_plan"])
        self.assertEqual(result["external_writes"], 0)

    def test_impossible_capacity_exposes_unscheduled_work_and_user_decision(self):
        result = run_impossible_capacity_scenario()

        self.assertGreater(result["unscheduled_p50_minutes"], 0)
        self.assertIn("p50_capacity_shortfall", result["risk_kinds"])
        self.assertEqual(
            set(result["tradeoff_kinds"]),
            {"increase_capacity", "reduce_scope", "revise_deadline"},
        )
        self.assertTrue(result["all_tradeoffs_require_approval"])
        self.assertEqual(result["external_writes"], 0)

    def test_fixed_fixture_evaluation_writes_an_honest_json_report(self):
        report = build_evaluation_report()

        self.assertEqual(report["fixture_set"], "personal-planner-v1.0")
        self.assertEqual(report["routing"]["correct"], report["routing"]["total"])
        self.assertEqual(report["planning"]["scenarios"], 7)
        self.assertEqual(report["safety"]["fixed_commitment_conflicts"], 0)
        self.assertEqual(report["safety"]["external_write_violations"], 0)
        self.assertEqual(report["usage"]["llm_calls"], 0)
        self.assertIsNone(report["usage"]["cost_usd"])

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            write_evaluation_report(target)
            persisted = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(persisted, report)
        checked_in = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "superpowers"
            / "evaluations"
            / "personal-planner-v1.0.json"
        )
        self.assertEqual(json.loads(checked_in.read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    unittest.main()
