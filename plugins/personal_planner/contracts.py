"""Validated contracts shared by Personal Planner domain analyzers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


VALID_DOMAINS = frozenset({"research", "career", "communication"})
VALID_ENERGY = frozenset({"low", "medium", "high"})
VALID_PROBABILITY = frozenset({"low", "medium", "high"})
EFFORT_RECONCILIATION_PERCENT = 10
EFFORT_RECONCILIATION_FLOOR_MINUTES = 15


class PlannerContractError(ValueError):
    """Raised when model-proposed Planner data violates a business invariant."""


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PlannerContractError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PlannerContractError(f"{field} must be an integer") from exc
    if result <= 0:
        raise PlannerContractError(f"{field} must be positive")
    return result


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PlannerContractError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PlannerContractError(f"{field} must be an integer") from exc
    if result < 0:
        raise PlannerContractError(f"{field} must not be negative")
    return result


def _aware_datetime(value: Any, field: str) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlannerContractError(f"{field} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise PlannerContractError(f"{field} must include a timezone")
    return parsed


def dependency_order(steps: list[dict[str, Any]]) -> list[str]:
    """Return a deterministic topological ordering of local step IDs."""

    ids = [str(step.get("local_id") or "").strip() for step in steps]
    if any(not step_id for step_id in ids):
        raise PlannerContractError("step local_id is required")
    if len(ids) != len(set(ids)):
        raise PlannerContractError("step local_id values must be unique")

    known = set(ids)
    incoming = {step_id: set() for step_id in ids}
    for step, step_id in zip(steps, ids, strict=True):
        for dependency in step.get("depends_on") or []:
            dependency_id = str(dependency).strip()
            if dependency_id not in known:
                raise PlannerContractError(
                    f"unknown dependency for {step_id}: {dependency_id}"
                )
            incoming[step_id].add(dependency_id)

    ready = sorted(step_id for step_id, values in incoming.items() if not values)
    result: list[str] = []
    while ready:
        step_id = ready.pop(0)
        result.append(step_id)
        for candidate in ids:
            if step_id not in incoming[candidate]:
                continue
            incoming[candidate].remove(step_id)
            if (
                not incoming[candidate]
                and candidate not in result
                and candidate not in ready
            ):
                ready.append(candidate)
                ready.sort()

    if len(result) != len(ids):
        raise PlannerContractError("step dependency graph contains a cycle")
    return result


def _normalize_steps(raw_steps: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlannerContractError("steps are required")

    steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise PlannerContractError(f"step {index} must be an object")
        step = dict(raw_step)
        step["local_id"] = str(step.get("local_id") or "").strip()
        step["title"] = str(step.get("title") or "").strip()
        if not step["local_id"] or not step["title"]:
            raise PlannerContractError("step local_id and title are required")
        step["p50_minutes"] = _positive_int(
            step.get("p50_minutes"), f"step {step['local_id']}.p50_minutes"
        )
        step["p90_minutes"] = _positive_int(
            step.get("p90_minutes"), f"step {step['local_id']}.p90_minutes"
        )
        if step["p50_minutes"] > step["p90_minutes"]:
            raise PlannerContractError(
                f"step {step['local_id']} p50_minutes must not exceed p90_minutes"
            )
        dependencies = step.get("depends_on") or []
        if not isinstance(dependencies, list):
            raise PlannerContractError(
                f"step {step['local_id']}.depends_on must be an array"
            )
        step["depends_on"] = [str(item).strip() for item in dependencies]
        step["energy"] = str(step.get("energy") or "medium").strip().lower()
        if step["energy"] not in VALID_ENERGY:
            raise PlannerContractError(f"step {step['local_id']} energy is invalid")
        step["splittable"] = bool(step.get("splittable", True))
        steps.append(step)

    dependency_order(steps)
    return steps


def _validate_effort_reconciliation(
    steps: list[dict[str, Any]], *, p50: int, p90: int
) -> None:
    for field, package_minutes in (("p50_minutes", p50), ("p90_minutes", p90)):
        step_minutes = sum(int(step[field]) for step in steps)
        tolerance = max(
            EFFORT_RECONCILIATION_FLOOR_MINUTES,
            round(package_minutes * EFFORT_RECONCILIATION_PERCENT / 100),
        )
        if abs(step_minutes - package_minutes) > tolerance:
            raise PlannerContractError(
                f"step {field} must reconcile with package effort within "
                f"{tolerance} minutes"
            )


def _normalize_risks(raw_risks: Any) -> list[dict[str, Any]]:
    if raw_risks is None:
        return []
    if not isinstance(raw_risks, list):
        raise PlannerContractError("risks must be an array")

    risks: list[dict[str, Any]] = []
    for index, raw_risk in enumerate(raw_risks):
        if not isinstance(raw_risk, dict):
            raise PlannerContractError(f"risk {index} must be an object")
        risk = dict(raw_risk)
        risk["kind"] = str(risk.get("kind") or "").strip()
        risk["probability"] = str(risk.get("probability") or "").strip().lower()
        if not risk["kind"] or risk["probability"] not in VALID_PROBABILITY:
            raise PlannerContractError(f"risk {index} is invalid")
        risk["buffer_minutes"] = _non_negative_int(
            risk.get("buffer_minutes", 0), f"risk {index}.buffer_minutes"
        )
        risks.append(risk)
    return risks


def validate_work_package(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize an untrusted WorkPackage proposal."""

    if not isinstance(payload, dict):
        raise PlannerContractError("work package must be an object")
    value = deepcopy(payload)

    domain = str(value.get("domain") or "").strip().lower()
    if domain not in VALID_DOMAINS:
        raise PlannerContractError("domain must be research, career, or communication")

    objective = str(value.get("objective") or "").strip()
    if not objective:
        raise PlannerContractError("objective is required")

    source_event_ids = list(
        dict.fromkeys(
            str(item).strip()
            for item in value.get("source_event_ids") or []
            if str(item).strip()
        )
    )
    if not source_event_ids:
        raise PlannerContractError("source_event_ids must retain event provenance")

    definition_of_done = [
        str(item).strip()
        for item in value.get("definition_of_done") or []
        if str(item).strip()
    ]
    if not definition_of_done:
        raise PlannerContractError("definition_of_done is required")

    hard_deadline = _aware_datetime(value.get("hard_deadline"), "hard_deadline")
    internal_raw = value.get("recommended_internal_deadline")
    internal_deadline = (
        _aware_datetime(internal_raw, "recommended_internal_deadline")
        if internal_raw
        else None
    )
    if internal_deadline and internal_deadline > hard_deadline:
        raise PlannerContractError("internal deadline cannot follow hard deadline")

    effort = dict(value.get("effort") or {})
    p50 = _positive_int(effort.get("p50_minutes"), "effort.p50_minutes")
    p90 = _positive_int(effort.get("p90_minutes"), "effort.p90_minutes")
    if p50 > p90:
        raise PlannerContractError(
            "effort.p50_minutes must not exceed effort.p90_minutes"
        )
    try:
        confidence = float(effort.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise PlannerContractError("effort.confidence must be a number") from exc
    if not 0.0 <= confidence <= 1.0:
        raise PlannerContractError("effort.confidence must be between 0 and 1")
    basis = str(effort.get("basis") or "").strip()
    if confidence < 0.75 and not basis:
        raise PlannerContractError("low-confidence effort requires a visible basis")

    steps = _normalize_steps(value.get("steps"))
    _validate_effort_reconciliation(steps, p50=p50, p90=p90)

    value.update(
        {
            "domain": domain,
            "objective": objective,
            "source_event_ids": source_event_ids,
            "definition_of_done": definition_of_done,
            "hard_deadline": hard_deadline.isoformat(),
            "recommended_internal_deadline": (
                internal_deadline.isoformat() if internal_deadline else None
            ),
            "effort": {
                **effort,
                "p50_minutes": p50,
                "p90_minutes": p90,
                "confidence": confidence,
                "basis": basis,
            },
            "steps": steps,
            "risks": _normalize_risks(value.get("risks")),
            "assumptions": [
                str(item).strip()
                for item in value.get("assumptions") or []
                if str(item).strip()
            ],
            "questions": [
                str(item).strip()
                for item in value.get("questions") or []
                if str(item).strip()
            ],
        }
    )
    return value


__all__ = [
    "PlannerContractError",
    "VALID_DOMAINS",
    "dependency_order",
    "validate_work_package",
]
