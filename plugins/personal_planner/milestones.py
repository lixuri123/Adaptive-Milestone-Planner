"""Deterministic day-level milestone allocation and estimate calibration."""

from __future__ import annotations

from math import ceil
from statistics import median

from .contracts import dependency_order, validate_work_package
from .timeutil import parse_datetime, resolve_timezone


def _round_five(value: float) -> int:
    return max(5, round(value / 5) * 5)


def calibrate_agent_work_package(
    package: dict, *, observations: list[dict], enabled: bool
) -> dict:
    """Calibrate a new agent estimate from at least three same-domain samples."""

    value = validate_work_package(package)
    ratios = sorted(
        float(item["actual_minutes"]) / int(item["credited_p50_minutes"])
        for item in observations
        if item.get("domain") == value["domain"]
        and item.get("actual_minutes") is not None
        and int(item.get("actual_minutes") or 0) > 0
        and int(item.get("credited_p50_minutes") or 0) > 0
    )
    applied = bool(enabled) and len(ratios) >= 3
    multiplier = min(1.5, max(0.75, float(median(ratios)))) if applied else 1.0
    if applied:
        for step in value["steps"]:
            step["p50_minutes"] = _round_five(step["p50_minutes"] * multiplier)
            step["p90_minutes"] = max(
                step["p50_minutes"],
                _round_five(step["p90_minutes"] * multiplier),
            )
        value["effort"]["p50_minutes"] = sum(
            step["p50_minutes"] for step in value["steps"]
        )
        value["effort"]["p90_minutes"] = sum(
            step["p90_minutes"] for step in value["steps"]
        )
        value["effort"]["confidence"] = min(
            0.9, float(value["effort"]["confidence"]) + 0.05
        )
        value["effort"]["basis"] = (
            f"{value['effort']['basis']}; domain pace calibrated from "
            f"{len(ratios)} check-ins"
        )
    value["calibration"] = {
        "source": "domain_checkins",
        "sample_count": len(ratios),
        "multiplier": multiplier,
        "applied": applied,
        "bounded_range": [0.75, 1.5],
    }
    return value


def _local_date(value: str, timezone_name: str) -> str:
    parsed = parse_datetime(value, timezone_name)
    if parsed is None:
        raise ValueError(f"invalid milestone deadline: {value}")
    return parsed.astimezone(resolve_timezone(timezone_name)).date().isoformat()


def _risk_rank(package: dict) -> int:
    ranks = {"low": 0, "medium": 1, "high": 2}
    return max(
        (
            ranks.get(str(risk.get("probability") or "low"), 0)
            for risk in package.get("risks") or []
        ),
        default=0,
    )


def build_milestone_plan(
    *,
    work_packages: list[dict],
    capacities: list[dict],
    progress_observations: list[dict] | None = None,
    timezone_name: str,
    max_daily_domains: int,
    protect_hard_deadline_day: bool,
) -> dict:
    """Allocate remaining P50 work into day-level targets without side effects."""

    max_daily_domains = max(1, min(int(max_daily_domains), 3))
    packages = [
        {**validate_work_package(value), "id": str(value["id"])}
        for value in work_packages
    ]
    credits: dict[str, dict[str, int]] = {}
    for observation in sorted(
        progress_observations or [],
        key=lambda value: (
            str(value["work_package_id"]),
            str(value["source_step_id"]),
            str(value.get("id") or ""),
        ),
    ):
        key = f"{observation['work_package_id']}:{observation['source_step_id']}"
        entry = credits.setdefault(
            key,
            {
                "p50": 0,
                "p90": 0,
                "adjustment_p50": 0,
                "adjustment_p90": 0,
                "chunk_index": 0,
            },
        )
        entry["p50"] += max(0, int(observation["credited_p50_minutes"]))
        entry["p90"] += max(0, int(observation["credited_p90_minutes"]))
        entry["adjustment_p50"] += int(
            observation.get("remaining_adjustment_p50_minutes") or 0
        )
        entry["adjustment_p90"] += int(
            observation.get("remaining_adjustment_p90_minutes") or 0
        )
        try:
            observed_chunk = int(str(observation["allocation_key"]).rsplit(":", 1)[1])
        except (KeyError, TypeError, ValueError):
            observed_chunk = 0
        if observation.get("outcome") == "partial":
            observed_chunk = max(0, observed_chunk - 1)
        entry["chunk_index"] = max(entry["chunk_index"], observed_chunk)

    state: dict[str, dict] = {}
    for package in packages:
        hard_date = _local_date(package["hard_deadline"], timezone_name)
        internal_date = _local_date(
            package.get("recommended_internal_deadline")
            or package["hard_deadline"],
            timezone_name,
        )
        step_map = {step["local_id"]: step for step in package["steps"]}
        for step_id in dependency_order(package["steps"]):
            step = step_map[step_id]
            key = f"{package['id']}:{step_id}"
            credited = credits.get(
                key,
                {
                    "p50": 0,
                    "p90": 0,
                    "adjustment_p50": 0,
                    "adjustment_p90": 0,
                    "chunk_index": 0,
                },
            )
            remaining_p50 = max(
                0,
                int(step["p50_minutes"])
                - credited["p50"]
                + credited["adjustment_p50"],
            )
            remaining_p90 = max(
                0,
                int(step["p90_minutes"])
                - credited["p90"]
                + credited["adjustment_p90"],
            )
            state[key] = {
                "key": key,
                "package_id": package["id"],
                "domain": package["domain"],
                "title": step["title"],
                "definition_of_done": package["definition_of_done"],
                "remaining_p50": remaining_p50,
                "remaining_p90": remaining_p90,
                "planning_p50": remaining_p50,
                "planning_p90": remaining_p90,
                "depends_on": {
                    f"{package['id']}:{dependency}"
                    for dependency in step.get("depends_on") or []
                },
                "splittable": bool(step["splittable"]),
                "internal_date": internal_date,
                "hard_date": hard_date,
                "risk_rank": _risk_rank(package),
                "chunk_index": credited["chunk_index"],
            }

    for item in state.values():
        item["downstream_count"] = sum(
            item["key"] in candidate["depends_on"] for candidate in state.values()
        )

    initial_p50 = sum(item["remaining_p50"] for item in state.values())
    initial_p90 = sum(item["remaining_p90"] for item in state.values())
    capacity_rows = sorted(
        (dict(value) for value in capacities), key=lambda value: value["date"]
    )
    milestones: list[dict] = []
    completed: set[str] = {
        key for key, item in state.items() if item["remaining_p50"] == 0
    }
    used_minutes = 0
    for day_index, capacity in enumerate(capacity_rows):
        day = str(capacity["date"])
        day_budget = max(0, int(int(capacity["capacity_minutes"]) / 5) * 5)
        domains: set[str] = set()
        allocated_today: set[str] = set()
        while day_budget > 0:
            ready = [
                item
                for item in state.values()
                if item["remaining_p50"] > 0
                and item["key"] not in allocated_today
                and item["depends_on"].issubset(completed)
                and day <= item["hard_date"]
                and not (
                    protect_hard_deadline_day and day == item["hard_date"]
                )
                and (
                    item["domain"] in domains
                    or len(domains) < max_daily_domains
                )
                and (
                    item["splittable"] or item["remaining_p50"] <= day_budget
                )
            ]
            if not ready:
                break
            ready.sort(
                key=lambda item: (
                    item["internal_date"],
                    item["hard_date"],
                    -item["downstream_count"],
                    -item["risk_rank"],
                    item["key"],
                )
            )
            item = ready[0]
            target_date = (
                item["internal_date"]
                if day <= item["internal_date"]
                else item["hard_date"]
            )
            eligible_future = [
                row
                for row in capacity_rows[day_index:]
                if str(row["date"]) <= target_date
                and not (
                    protect_hard_deadline_day
                    and str(row["date"]) == item["hard_date"]
                )
            ]
            future_after_today = sum(
                max(0, int(row["capacity_minutes"]))
                for row in eligible_future
                if str(row["date"]) != day
            )
            required_today = max(0, item["remaining_p50"] - future_after_today)
            pace_basis = (
                item["remaining_p90"]
                if item["risk_rank"] >= 1
                else item["remaining_p50"]
            )
            pace = max(
                30,
                ceil(pace_basis / max(len(eligible_future), 1) / 5) * 5,
                required_today,
            )
            minutes = min(day_budget, item["remaining_p50"], pace)
            if not item["splittable"]:
                minutes = item["remaining_p50"]
            minutes = int(minutes / 5) * 5
            if minutes <= 0 or minutes > day_budget:
                allocated_today.add(item["key"])
                continue

            item["chunk_index"] += 1
            milestones.append(
                {
                    "allocation_key": (
                        f"{item['key']}:{item['chunk_index']:03d}"
                    ),
                    "date": day,
                    "window_end": None,
                    "domain": item["domain"],
                    "work_package_id": item["package_id"],
                    "title": item["title"],
                    "target_minutes": minutes,
                    "definition_of_done": item["definition_of_done"],
                    "source_step_ids": [item["key"].split(":", 1)[1]],
                    "reminder_anchor": dict(
                        capacity.get("reminder_anchor") or {}
                    ),
                    "explanation": [
                        f"Internal deadline: {item['internal_date']}",
                        f"Hard deadline: {item['hard_date']}",
                    ],
                    "status": "draft",
                }
            )
            original_p50 = item["remaining_p50"]
            original_p90 = item["remaining_p90"]
            item["remaining_p50"] -= minutes
            proportional_p90 = min(
                original_p90,
                max(minutes, round(minutes * original_p90 / max(original_p50, 1))),
            )
            item["remaining_p90"] -= proportional_p90
            day_budget -= minutes
            used_minutes += minutes
            domains.add(item["domain"])
            allocated_today.add(item["key"])
            if item["remaining_p50"] == 0:
                completed.add(item["key"])

    unscheduled = [
        {
            "step_id": item["key"],
            "work_package_id": item["package_id"],
            "title": item["title"],
            "remaining_p50_minutes": item["remaining_p50"],
            "remaining_p90_minutes": item["remaining_p90"],
            "hard_deadline": item["hard_date"],
        }
        for item in sorted(state.values(), key=lambda value: value["key"])
        if item["remaining_p50"] > 0
    ]
    deadline_risks = [
        {**item, "kind": "p50_capacity_shortfall", "severity": "critical"}
        for item in unscheduled
    ]
    p90_buffer_minutes = sum(
        max(0, item["planning_p90"] - item["planning_p50"])
        for item in state.values()
        if item["remaining_p50"] == 0
    )
    total_capacity = sum(
        max(0, int(day["capacity_minutes"])) for day in capacity_rows
    )
    unused_capacity = total_capacity - used_minutes
    if p90_buffer_minutes > unused_capacity:
        deadline_risks.append(
            {
                "kind": "p90_buffer_shortfall",
                "severity": "warning",
                "remaining_p90_buffer_minutes": p90_buffer_minutes,
                "unused_capacity_minutes": max(0, unused_capacity),
            }
        )

    package_order = sorted(
        packages,
        key=lambda package: (
            _local_date(
                package.get("recommended_internal_deadline")
                or package["hard_deadline"],
                timezone_name,
            ),
            _local_date(package["hard_deadline"], timezone_name),
            -_risk_rank(package),
            package["id"],
        ),
    )
    decision_policy = {
        "authority": "single_aggregator",
        "max_daily_domains": max_daily_domains,
        "protect_hard_deadline_day": bool(protect_hard_deadline_day),
        "priority_order": [package["id"] for package in package_order],
        "rationale": {
            package["id"]: [
                "internal_deadline="
                f"{package.get('recommended_internal_deadline') or package['hard_deadline']}",
                f"hard_deadline={package['hard_deadline']}",
                f"risk_rank={_risk_rank(package)}",
            ]
            for package in package_order
        },
    }
    shortfall = sum(item["remaining_p50_minutes"] for item in unscheduled)
    tradeoff_options: list[dict] = []
    if shortfall:
        tradeoff_options = [
            {
                "kind": "reduce_scope",
                "required_minutes": shortfall,
                "requires_approval": True,
            },
            {
                "kind": "revise_deadline",
                "required_minutes": shortfall,
                "requires_approval": True,
            },
            {
                "kind": "increase_capacity",
                "required_minutes": shortfall,
                "requires_approval": True,
            },
        ]
        if len({item["work_package_id"] for item in unscheduled}) > 1:
            tradeoff_options.append(
                {
                    "kind": "lower_competing_goal",
                    "required_minutes": shortfall,
                    "requires_approval": True,
                }
            )
    return {
        "milestones": milestones,
        "unscheduled": unscheduled,
        "deadline_risks": deadline_risks,
        "decision_policy": decision_policy,
        "tradeoff_options": tradeoff_options,
        "capacity": {
            "available_minutes": total_capacity,
            "scheduled_minutes": used_minutes,
        },
        "p50_remaining_minutes": initial_p50,
        "p90_remaining_minutes": initial_p90,
        "unallocated_p50_minutes": sum(
            item["remaining_p50"] for item in state.values()
        ),
        "unallocated_p90_minutes": sum(
            item["remaining_p90"]
            for item in state.values()
            if item["remaining_p50"] > 0
        ),
    }


def diff_milestone_plans(current: dict | None, proposed: dict) -> dict:
    """Return a stable user-reviewable diff between milestone plan versions."""

    def key(item: dict) -> str:
        return str(item.get("allocation_key") or item["id"])

    old = {key(item): item for item in (current or {}).get("milestones", [])}
    new = {key(item): item for item in proposed.get("milestones", [])}
    return {
        "added": [new[value] for value in sorted(new.keys() - old.keys())],
        "removed": [old[value] for value in sorted(old.keys() - new.keys())],
        "changed": [
            {"before": old[value], "after": new[value]}
            for value in sorted(old.keys() & new.keys())
            if (
                old[value].get("date"),
                old[value].get("target_minutes"),
                old[value].get("status"),
            )
            != (
                new[value].get("date"),
                new[value].get("target_minutes"),
                new[value].get("status"),
            )
        ],
    }


__all__ = [
    "build_milestone_plan",
    "calibrate_agent_work_package",
    "diff_milestone_plans",
]
