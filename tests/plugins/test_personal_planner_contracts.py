from __future__ import annotations

import unittest

from plugins.personal_planner.contracts import (
    PlannerContractError,
    dependency_order,
    validate_work_package,
)


VALID_PACKAGE = {
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


class WorkPackageContractTest(unittest.TestCase):
    def test_valid_package_is_normalized_without_mutating_input(self):
        payload = {**VALID_PACKAGE, "objective": "  Complete experiment 1  "}

        result = validate_work_package(payload)

        self.assertEqual(result["objective"], "Complete experiment 1")
        self.assertEqual(payload["objective"], "  Complete experiment 1  ")
        self.assertEqual(dependency_order(result["steps"]), ["prepare", "run"])

    def test_rejects_p50_above_p90(self):
        payload = {
            **VALID_PACKAGE,
            "effort": {**VALID_PACKAGE["effort"], "p50_minutes": 400},
        }

        with self.assertRaisesRegex(PlannerContractError, "p50_minutes"):
            validate_work_package(payload)

    def test_rejects_unknown_dependency(self):
        steps = [dict(VALID_PACKAGE["steps"][1], depends_on=["missing"])]

        with self.assertRaisesRegex(PlannerContractError, "unknown dependency"):
            validate_work_package({**VALID_PACKAGE, "steps": steps})

    def test_rejects_dependency_cycle(self):
        steps = [
            dict(VALID_PACKAGE["steps"][0], depends_on=["run"]),
            dict(VALID_PACKAGE["steps"][1], depends_on=["prepare"]),
        ]

        with self.assertRaisesRegex(PlannerContractError, "cycle"):
            validate_work_package({**VALID_PACKAGE, "steps": steps})

    def test_rejects_internal_deadline_after_hard_deadline(self):
        payload = {
            **VALID_PACKAGE,
            "recommended_internal_deadline": "2026-08-01T18:00:00+08:00",
        }

        with self.assertRaisesRegex(PlannerContractError, "internal deadline"):
            validate_work_package(payload)

    def test_rejects_step_effort_that_does_not_reconcile(self):
        steps = [dict(VALID_PACKAGE["steps"][0], p50_minutes=30)]

        with self.assertRaisesRegex(PlannerContractError, "reconcile"):
            validate_work_package({**VALID_PACKAGE, "steps": steps})

    def test_low_confidence_requires_visible_estimate_basis(self):
        effort = {**VALID_PACKAGE["effort"], "basis": ""}

        with self.assertRaisesRegex(PlannerContractError, "basis"):
            validate_work_package({**VALID_PACKAGE, "effort": effort})

    def test_external_package_requires_source_event_provenance(self):
        with self.assertRaisesRegex(PlannerContractError, "source_event_ids"):
            validate_work_package({**VALID_PACKAGE, "source_event_ids": []})


if __name__ == "__main__":
    unittest.main()
