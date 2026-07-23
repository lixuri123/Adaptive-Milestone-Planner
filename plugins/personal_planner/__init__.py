"""Personal Planner plugin.

The plugin deliberately owns all of its state and business logic.  Hermes core
only sees the normal plugin registration surfaces and the public Cron API.
"""

from __future__ import annotations

from .cli import planner_command, register_cli
from .commands import handle_plan_command
from .expert_agents import PlannerDomainAgentRunner
from .runtime_context import planner_pre_llm_context
from .tool import PERSONAL_PLANNER_SCHEMA, handle_personal_planner_tool


def register(ctx) -> None:
    """Register the isolated Planner surfaces with Hermes."""
    expert_runner = PlannerDomainAgentRunner(ctx.llm)
    ctx.register_cli_command(
        name="plan",
        help="Capture commitments and manage personal reminders",
        setup_fn=register_cli,
        handler_fn=planner_command,
        description=(
            "Profile-scoped personal planning inbox, acknowledgement-aware "
            "reminders, confirmed profile facts, explainable information-radar "
            "proposals, read-only Gmail ingestion, long-term goals, "
            "calendar-aware rolling schedules, time-block check-ins, and daily "
            "reviews."
        ),
    )
    ctx.register_command(
        name="plan",
        handler=lambda raw_args: handle_plan_command(
            raw_args, expert_runner=expert_runner
        ),
        description="Capture commitments and manage calendar-aware personal plans",
        args_hint=(
            "[inbox|profile|radar-propose|monitors|goals|tasks|schedule|"
            "review|today|capture|confirm|done]"
        ),
    )
    ctx.register_tool(
        name="personal_planner",
        toolset="personal_planner",
        schema=PERSONAL_PLANNER_SCHEMA,
        handler=lambda args, **_: handle_personal_planner_tool(
            args, expert_runner=expert_runner
        ),
        check_fn=lambda: True,
        emoji="🗓️",
    )
    ctx.register_hook("pre_llm_call", planner_pre_llm_context)
