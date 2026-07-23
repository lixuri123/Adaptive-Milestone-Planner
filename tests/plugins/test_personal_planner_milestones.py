from __future__ import annotations

import copy
import unittest

from plugins.personal_planner.milestones import (
    build_milestone_plan,
    calibrate_agent_work_package,
    diff_milestone_plans,
)


def capacity(day: str, minutes: int) -> dict:
    return {
        "date": day,
        "capacity_minutes": minutes,
        "capacity_class": "low" if minutes < 90 else "medium",
        "reminder_anchor": {"kind": "first_suitable_window"},
    }


def work_package(
    *,
    package_id: str,
    domain: str = "research",
    objective: str = "Research work",
    hard_deadline: str = "2026-07-31T18:00:00+08:00",
    internal_deadline: str | None = None,
    p50_minutes: int = 120,
    p90_minutes: int = 180,
    risks: list[dict] | None = None,
) -> dict:
    return {
        "id": package_id,
        "domain": domain,
        "objective": objective,
        "source_event_ids": [f"EV-{package_id}"],
        "definition_of_done": ["Result recorded"],
        "hard_deadline": hard_deadline,
        "recommended_internal_deadline": internal_deadline or hard_deadline,
        "effort": {
            "p50_minutes": p50_minutes,
            "p90_minutes": p90_minutes,
            "confidence": 0.5,
            "basis": "Scenario fixture estimate",
        },
        "steps": [
            {
                "local_id": "work",
                "title": f"Do {domain} work",
                "p50_minutes": p50_minutes,
                "p90_minutes": p90_minutes,
                "depends_on": [],
                "energy": "high",
                "splittable": True,
            }
        ],
        "risks": risks or [],
        "assumptions": [],
        "questions": [],
    }


class MilestoneAllocationTest(unittest.TestCase):
    def test_mixed_work_keeps_busy_days_light_and_protects_own_deadlines(self):
        packages = [
            work_package(
                package_id="WP-C",
                domain="career",
                objective="Submit two internships",
                hard_deadline="2026-07-25T23:59:00+08:00",
                internal_deadline="2026-07-24T23:59:00+08:00",
                p50_minutes=360,
                p90_minutes=480,
            ),
            work_package(
                package_id="WP-R",
                objective="Complete three experiments",
                hard_deadline="2026-07-31T18:00:00+08:00",
                internal_deadline="2026-07-30T18:00:00+08:00",
                p50_minutes=1080,
                p90_minutes=1440,
                risks=[
                    {
                        "kind": "rerun",
                        "probability": "medium",
                        "buffer_minutes": 180,
                    }
                ],
            ),
        ]
        capacities = [
            capacity(f"2026-07-{day:02d}", 60 if day in {21, 22} else 240)
            for day in range(20, 32)
        ]

        plan = build_milestone_plan(
            work_packages=packages,
            capacities=capacities,
            timezone_name="Asia/Shanghai",
            max_daily_domains=2,
            protect_hard_deadline_day=True,
        )

        totals: dict[str, int] = {}
        for item in plan["milestones"]:
            totals[item["date"]] = totals.get(item["date"], 0) + item["target_minutes"]
        self.assertLessEqual(totals.get("2026-07-21", 0), 60)
        self.assertLessEqual(totals.get("2026-07-22", 0), 60)
        self.assertFalse(
            any(
                item["work_package_id"] == "WP-C" and item["date"] == "2026-07-25"
                for item in plan["milestones"]
            )
        )
        self.assertFalse(
            any(
                item["work_package_id"] == "WP-R" and item["date"] == "2026-07-31"
                for item in plan["milestones"]
            )
        )
        self.assertTrue(
            any(
                item["work_package_id"] == "WP-R" and item["date"] < "2026-07-25"
                for item in plan["milestones"]
            )
        )
        self.assertEqual(plan["unallocated_p50_minutes"], 0)

    def test_protected_deadline_day_remains_available_to_unrelated_work(self):
        career = work_package(
            package_id="WP-C",
            domain="career",
            hard_deadline="2026-07-21T18:00:00+08:00",
            internal_deadline="2026-07-20T18:00:00+08:00",
            p50_minutes=60,
            p90_minutes=90,
        )
        research = work_package(
            package_id="WP-R",
            hard_deadline="2026-07-22T18:00:00+08:00",
            p50_minutes=60,
            p90_minutes=90,
        )

        plan = build_milestone_plan(
            work_packages=[career, research],
            capacities=[capacity("2026-07-21", 60)],
            timezone_name="Asia/Shanghai",
            max_daily_domains=2,
            protect_hard_deadline_day=True,
        )

        self.assertEqual(len(plan["milestones"]), 1)
        self.assertEqual(plan["milestones"][0]["work_package_id"], "WP-R")

    def test_impossible_capacity_reports_shortfall_and_approval_tradeoffs(self):
        plan = build_milestone_plan(
            work_packages=[
                work_package(
                    package_id="WP-1",
                    objective="Large task",
                    hard_deadline="2026-07-21T18:00:00+08:00",
                    internal_deadline="2026-07-20T18:00:00+08:00",
                    p50_minutes=600,
                    p90_minutes=900,
                )
            ],
            capacities=[capacity("2026-07-20", 60)],
            timezone_name="Asia/Shanghai",
            max_daily_domains=2,
            protect_hard_deadline_day=True,
        )

        self.assertEqual(plan["unscheduled"][0]["remaining_p50_minutes"], 540)
        self.assertEqual(plan["deadline_risks"][0]["kind"], "p50_capacity_shortfall")
        self.assertEqual(plan["decision_policy"]["authority"], "single_aggregator")
        self.assertEqual(
            {option["kind"] for option in plan["tradeoff_options"]},
            {"increase_capacity", "reduce_scope", "revise_deadline"},
        )
        self.assertTrue(
            all(option["requires_approval"] for option in plan["tradeoff_options"])
        )

    def test_p90_uncertainty_is_reported_when_p50_fits_without_enough_buffer(self):
        plan = build_milestone_plan(
            work_packages=[
                work_package(
                    package_id="WP-P90",
                    p50_minutes=120,
                    p90_minutes=240,
                )
            ],
            capacities=[capacity("2026-07-20", 120)],
            timezone_name="Asia/Shanghai",
            max_daily_domains=2,
            protect_hard_deadline_day=True,
        )

        self.assertEqual(plan["unallocated_p50_minutes"], 0)
        p90_risk = next(
            risk
            for risk in plan["deadline_risks"]
            if risk["kind"] == "p90_buffer_shortfall"
        )
        self.assertEqual(p90_risk["remaining_p90_buffer_minutes"], 120)
        self.assertEqual(p90_risk["unused_capacity_minutes"], 0)

    def test_progress_observations_reduce_remaining_work_before_replanning(self):
        package = work_package(package_id="WP-1")

        plan = build_milestone_plan(
            work_packages=[package],
            capacities=[capacity("2026-07-21", 60)],
            progress_observations=[
                {
                    "work_package_id": "WP-1",
                    "source_step_id": "work",
                    "allocation_key": "WP-1:work:001",
                    "credited_p50_minutes": 60,
                    "credited_p90_minutes": 90,
                    "outcome": "partial",
                }
            ],
            timezone_name="Asia/Shanghai",
            max_daily_domains=2,
            protect_hard_deadline_day=True,
        )

        self.assertEqual(sum(x["target_minutes"] for x in plan["milestones"]), 60)
        self.assertEqual(plan["p50_remaining_minutes"], 60)
        self.assertEqual(plan["unallocated_p50_minutes"], 0)
        self.assertEqual(plan["milestones"][0]["allocation_key"], "WP-1:work:001")

    def test_dependency_order_and_non_splittable_steps_are_preserved(self):
        package = work_package(package_id="WP-DEP", p50_minutes=150, p90_minutes=210)
        package["steps"] = [
            {
                "local_id": "prepare",
                "title": "Prepare",
                "p50_minutes": 30,
                "p90_minutes": 45,
                "depends_on": [],
                "energy": "medium",
                "splittable": True,
            },
            {
                "local_id": "run",
                "title": "Run",
                "p50_minutes": 120,
                "p90_minutes": 165,
                "depends_on": ["prepare"],
                "energy": "high",
                "splittable": False,
            },
        ]

        plan = build_milestone_plan(
            work_packages=[package],
            capacities=[capacity("2026-07-20", 90), capacity("2026-07-21", 120)],
            timezone_name="Asia/Shanghai",
            max_daily_domains=2,
            protect_hard_deadline_day=True,
        )

        by_step = {item["source_step_ids"][0]: item for item in plan["milestones"]}
        self.assertEqual(by_step["prepare"]["date"], "2026-07-20")
        self.assertEqual(by_step["run"]["date"], "2026-07-21")
        self.assertEqual(by_step["run"]["target_minutes"], 120)

    def test_domain_calibration_requires_three_samples_and_is_bounded(self):
        package = work_package(package_id="WP-new")
        observation = {
            "domain": "research",
            "credited_p50_minutes": 60,
            "actual_minutes": 90,
        }

        unchanged = calibrate_agent_work_package(
            package, observations=[observation], enabled=True
        )
        calibrated = calibrate_agent_work_package(
            package, observations=[observation] * 3, enabled=True
        )

        self.assertEqual(unchanged["effort"]["p50_minutes"], 120)
        self.assertEqual(calibrated["effort"]["p50_minutes"], 180)
        self.assertEqual(calibrated["calibration"]["sample_count"], 3)
        self.assertEqual(calibrated["calibration"]["multiplier"], 1.5)
        self.assertEqual(package["effort"]["p50_minutes"], 120)

    def test_plan_and_diff_are_deterministic_without_mutating_inputs(self):
        packages = [work_package(package_id="WP-1")]
        capacities = [capacity("2026-07-20", 60), capacity("2026-07-21", 60)]
        original = copy.deepcopy((packages, capacities))
        kwargs = {
            "work_packages": packages,
            "capacities": capacities,
            "timezone_name": "Asia/Shanghai",
            "max_daily_domains": 2,
            "protect_hard_deadline_day": True,
        }

        first = build_milestone_plan(**kwargs)
        second = build_milestone_plan(**kwargs)
        proposed = copy.deepcopy(first)
        proposed["milestones"][0]["target_minutes"] = 30
        difference = diff_milestone_plans(first, proposed)

        self.assertEqual(first, second)
        self.assertEqual((packages, capacities), original)
        self.assertEqual(len(difference["changed"]), 1)
        self.assertEqual(difference["added"], [])
        self.assertEqual(difference["removed"], [])


if __name__ == "__main__":
    unittest.main()
