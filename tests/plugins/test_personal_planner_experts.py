from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from plugins.personal_planner.expert_agents import (
    PlannerDomainAgentRunner,
    PlannerExpertError,
    WORK_PACKAGE_JSON_SCHEMA,
    build_delegated_expert_goal,
    explain_domain_routing,
    route_domain_agents,
    validate_delegated_expert_result,
)


VALID_PACKAGE = {
    "domain": "research",
    "objective": "Run experiment",
    "source_event_ids": ["EV-1"],
    "definition_of_done": ["Result recorded"],
    "hard_deadline": "2026-07-31T18:00:00+08:00",
    "recommended_internal_deadline": "2026-07-29T18:00:00+08:00",
    "effort": {
        "p50_minutes": 120,
        "p90_minutes": 180,
        "confidence": 0.6,
        "basis": "Initial domain estimate",
    },
    "steps": [
        {
            "local_id": "run",
            "title": "Run experiment",
            "p50_minutes": 120,
            "p90_minutes": 180,
            "depends_on": [],
            "energy": "high",
            "splittable": False,
        }
    ],
    "risks": [],
    "assumptions": [],
    "questions": [],
}


class FakeLlm:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed=self.parsed,
            provider="test",
            model="test-model",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                cost_usd=0.01,
            ),
        )


class DomainAgentTest(unittest.TestCase):
    def test_sparse_router_selects_research_and_career_only(self):
        text = "Finish three experiments and submit two internship applications"

        selected = route_domain_agents(text, source_kind="conversation")
        reasons = explain_domain_routing(text, "conversation", selected)

        self.assertEqual(selected, ["research", "career"])
        self.assertIn("matched_term:experiment", reasons["research"])
        self.assertIn("matched_term:internship", reasons["career"])
        self.assertNotIn("communication", reasons)

    def test_external_notice_routes_communication_first_and_deduplicates_it(self):
        selected = route_domain_agents(
            "Internship registration notice from the school", source_kind="email"
        )
        reasons = explain_domain_routing(
            "Internship registration notice from the school", "email", selected
        )

        self.assertEqual(selected, ["communication", "career"])
        self.assertEqual(selected.count("communication"), 1)
        self.assertIn("external_source:email", reasons["communication"])

    def test_unmatched_conversation_uses_communication_as_safe_fallback(self):
        selected = route_domain_agents("Please help me handle this", "conversation")

        self.assertEqual(selected, ["communication"])
        self.assertEqual(
            explain_domain_routing("Please help me handle this", "conversation", selected),
            {"communication": ["fallback:no_domain_term_matched"]},
        )

    def test_runner_calls_structured_llm_once_and_validates_package(self):
        llm = FakeLlm(VALID_PACKAGE)
        runner = PlannerDomainAgentRunner(llm)
        event = {
            "id": "EV-1",
            "event_type": "work.requested",
            "payload": {"text": "run experiment"},
        }

        result = runner.analyze(
            "research", event=event, snapshot={"profile": [], "goals": []}
        )

        self.assertEqual(result["package"]["domain"], "research")
        self.assertEqual(result["usage"]["total_tokens"], 30)
        self.assertEqual(len(llm.calls), 1)
        call = llm.calls[0]
        self.assertEqual(call["purpose"], "personal_planner.research_analysis")
        self.assertIs(call["json_schema"], WORK_PACKAGE_JSON_SCHEMA)
        self.assertEqual(json.loads(call["input"][0]["text"])["event"], event)

    def test_runner_rejects_clarification_or_wrong_domain_output(self):
        with self.assertRaisesRegex(PlannerExpertError, "clarification"):
            PlannerDomainAgentRunner(FakeLlm(None)).analyze(
                "research",
                event={"id": "EV-1", "payload": {}},
                snapshot={},
            )

        career_package = {**VALID_PACKAGE, "domain": "career"}
        with self.assertRaisesRegex(PlannerExpertError, "domain"):
            PlannerDomainAgentRunner(FakeLlm(career_package)).analyze(
                "research",
                event={"id": "EV-1", "payload": {}},
                snapshot={},
            )

    def test_delegate_goal_is_bounded_and_forbids_writes(self):
        goal = build_delegated_expert_goal(
            "research",
            event={"id": "EV-1", "payload": {"text": "run experiment"}},
            snapshot={"profile": [], "goals": []},
        )

        self.assertIn("Return exactly one JSON object", goal)
        self.assertIn("must not modify", goal)
        self.assertIn("read-only Hermes tools", goal)
        self.assertNotIn("conversation_history", goal)

    def test_delegated_result_must_be_plain_valid_json_for_requested_domain(self):
        result = validate_delegated_expert_result(
            "research", json.dumps(VALID_PACKAGE)
        )
        self.assertEqual(result["objective"], "Run experiment")

        with self.assertRaisesRegex(PlannerExpertError, "plain WorkPackage JSON"):
            validate_delegated_expert_result(
                "research", f"```json\n{json.dumps(VALID_PACKAGE)}\n```"
            )
        with self.assertRaisesRegex(PlannerExpertError, "domain"):
            validate_delegated_expert_result(
                "career", json.dumps(VALID_PACKAGE)
            )

    def test_plugin_binds_one_runner_to_command_and_tool_handlers(self):
        from plugins import personal_planner

        llm = object()
        ctx = SimpleNamespace(
            llm=llm,
            cli=[],
            commands=[],
            tools=[],
            hooks=[],
            register_cli_command=lambda **kw: ctx.cli.append(kw),
            register_command=lambda **kw: ctx.commands.append(kw),
            register_tool=lambda **kw: ctx.tools.append(kw),
            register_hook=lambda *args: ctx.hooks.append(args),
        )
        personal_planner.register(ctx)

        with (
            patch(
                "plugins.personal_planner.handle_plan_command",
                return_value="command-result",
            ) as command,
            patch(
                "plugins.personal_planner.handle_personal_planner_tool",
                return_value="tool-result",
            ) as tool,
        ):
            self.assertEqual(ctx.commands[0]["handler"]("analyze work"), "command-result")
            self.assertEqual(
                ctx.tools[0]["handler"]({"action": "analyze"}), "tool-result"
            )

        command_runner = command.call_args.kwargs["expert_runner"]
        tool_runner = tool.call_args.kwargs["expert_runner"]
        self.assertIs(command_runner, tool_runner)
        self.assertIs(command_runner.llm, llm)


if __name__ == "__main__":
    unittest.main()
