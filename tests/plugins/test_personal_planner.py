from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from plugins.personal_planner.calendar_sync import GoogleWorkspaceCalendar
from plugins.personal_planner.commands import handle_plan_command
from plugins.personal_planner.config import PlannerSettings, load_settings
from plugins.personal_planner.cron_support import (
    AGENT_DAILY_JOB_NAME,
    AGENT_DAILY_PROMPT,
    AGENT_WEEKLY_JOB_NAME,
    AGENT_WEEKLY_PROMPT,
    DISPATCH_JOB_NAME,
    INGEST_JOB_NAME,
    RADAR_JOB_NAME,
    RADAR_PROMPT,
    REVIEW_JOB_NAME,
    automation_status,
    ensure_automation_jobs,
    ensure_agent_daily_job,
    ensure_agent_weekly_job,
    ensure_dispatch_job,
    ensure_ingest_job,
    ensure_radar_job,
    ensure_review_job,
    pause_automation_jobs,
)
from plugins.personal_planner.gmail_ingest import (
    GoogleWorkspaceGmail,
    render_ingest_message,
)
from plugins.personal_planner.service import PlannerError, PlannerService
from plugins.personal_planner.store import SCHEMA_VERSION, PlannerStore
from plugins.personal_planner.tool import (
    PERSONAL_PLANNER_SCHEMA,
    handle_personal_planner_tool,
)


NOW = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)


class FakeCalendar:
    def __init__(self, events=None):
        self.calls = []
        self.list_calls = []
        self.events = list(events or [])

    def create_event(self, **values):
        self.calls.append(values)
        event_id = f"event-{len(self.calls)}"
        return {
            "status": "created",
            "id": event_id,
            "summary": values["summary"],
            "htmlLink": f"https://calendar.example/{event_id}",
        }

    def list_events(self, **values):
        self.list_calls.append(values)
        return list(self.events)


class FailingCalendar:
    def create_event(self, **values):
        raise RuntimeError("calendar unavailable")

    def list_events(self, **values):
        raise RuntimeError("calendar unavailable")


class FakeGmail:
    def __init__(self, messages=None):
        self.messages = {
            value["id"]: dict(value) for value in (messages or [])
        }
        self.search_calls = []
        self.get_calls = []
        self.fail_search = False
        self.fail_get = set()

    def search_messages(self, **values):
        self.search_calls.append(values)
        if self.fail_search:
            raise RuntimeError("Gmail unavailable")
        return [
            {
                key: message.get(key)
                for key in (
                    "id",
                    "threadId",
                    "from",
                    "to",
                    "subject",
                    "date",
                    "snippet",
                    "labels",
                )
            }
            for message in self.messages.values()
        ][: values["max_results"]]

    def get_message(self, *, message_id):
        self.get_calls.append(message_id)
        if message_id in self.fail_get:
            raise RuntimeError("message unavailable")
        return dict(self.messages[message_id])


class PersonalPlannerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / ".hermes"
        self.home.mkdir()
        self.env = patch.dict("os.environ", {"HERMES_HOME": str(self.home)})
        self.env.start()
        self.store = PlannerStore(self.home / "planner" / "planner.db")
        self.service = PlannerService(
            store=self.store,
            settings=PlannerSettings(timezone="Asia/Shanghai"),
            now_fn=lambda: NOW,
        )

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def _draft(self, **overrides):
        values = {
            "raw_content": "六级报名 9 月 10 日开放，9 月 18 日截止",
            "title": "六级报名",
            "actionable_at": "2026-09-10T09:00:00+08:00",
            "due_at": "2026-09-18T17:00:00+08:00",
            "event_at": "2026-12-14T09:00:00+08:00",
            "importance": "high",
        }
        values.update(overrides)
        return self.service.capture(**values)

    def _confirmed_plan(
        self,
        *,
        estimated_minutes=60,
        min_block_minutes=30,
        title="Focused work",
    ):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="high",
            label="Test focus",
        )
        task = self.service.create_task(
            title=title,
            estimated_minutes=estimated_minutes,
            min_block_minutes=min_block_minutes,
            energy="high",
        )
        draft = self.service.schedule_preview(days=1)
        confirmed = self.service.confirm_schedule(draft["id"], confirmed=True)
        return task, confirmed

    def _gmail_message(self, message_id="msg-1", **overrides):
        values = {
            "id": message_id,
            "threadId": "thread-1",
            "from": "Graduate School <grad@example.edu>",
            "to": "student@example.edu",
            "subject": "CET-6 registration notice",
            "date": "Wed, 15 Jul 2026 08:00:00 +0800",
            "snippet": "Registration will open soon.",
            "labels": ["INBOX"],
            "body": "Please review the registration window and requirements.",
        }
        values.update(overrides)
        return values

    def _confirmed_fact(self, category, fact_key, fact_value, **overrides):
        fact = self.service.add_profile_fact(
            category=category,
            fact_key=fact_key,
            fact_value=fact_value,
            **overrides,
        )
        return self.service.confirm_profile_fact(fact["id"])

    def test_bundled_plugin_discovery_loads_all_planner_surfaces(self):
        import yaml
        from hermes_cli.plugins import PluginManager

        (self.home / "config.yaml").write_text(
            yaml.safe_dump({"plugins": {"enabled": ["personal_planner"]}}),
            encoding="utf-8",
        )
        manager = PluginManager()
        manager.discover_and_load()

        loaded = manager._plugins["personal_planner"]
        self.assertTrue(loaded.enabled, loaded.error)
        self.assertIn("plan", manager._cli_commands)
        self.assertIn("plan", manager._plugin_commands)
        self.assertIn("personal_planner", manager._plugin_tool_names)
        self.assertTrue(manager.has_hook("pre_llm_call"))
        hook_results = manager.invoke_hook(
            "pre_llm_call",
            session_id="planner-test",
            user_message="plan my week",
        )
        self.assertEqual(len(hook_results), 1)
        self.assertIn("personal_planner_runtime", hook_results[0]["context"])

    def test_capture_is_draft_and_confirm_builds_nudges(self):
        item = self._draft()
        self.assertEqual(item["status"], "draft")
        self.assertEqual(self.store.list_nudges(item["id"]), [])

        confirmed = self.service.confirm(item["id"])
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertGreaterEqual(len(confirmed["nudges"]), 4)
        self.assertTrue(any(n["ack_required"] for n in confirmed["nudges"]))

        confirmed_again = self.service.confirm(item["id"])
        self.assertEqual(
            [n["id"] for n in confirmed["nudges"]],
            [n["id"] for n in confirmed_again["nudges"]],
        )

    def test_overview_composes_bounded_desktop_snapshot(self):
        draft = self._draft()
        fact = self.service.add_profile_fact(
            category="education",
            fact_key="school",
            fact_value="北京邮电大学",
        )
        self.service.confirm_profile_fact(fact["id"])
        goal = self.service.create_goal(title="Find an internship", area="career")
        task = self.service.create_task(title="Review roles", goal_id=goal["id"])

        snapshot = self.service.overview()

        self.assertGreaterEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["status"]["timezone"], "Asia/Shanghai")
        self.assertEqual(snapshot["items"][0]["id"], draft["id"])
        self.assertEqual(snapshot["goals"][0]["id"], goal["id"])
        self.assertEqual(snapshot["tasks"][0]["id"], task["id"])
        self.assertEqual(snapshot["profile_facts"][0]["status"], "confirmed")
        self.assertIsNone(snapshot["active_schedule"])
        self.assertIn("today_milestones", snapshot)
        self.assertIn("work_packages", snapshot)
        self.assertIn("recent_agent_runs", snapshot)

        command_snapshot = json.loads(handle_plan_command("overview"))
        self.assertGreaterEqual(command_snapshot["schema_version"], 2)
        self.assertEqual(command_snapshot["items"][0]["id"], draft["id"])

    def test_desktop_rich_goal_command_preserves_planning_fields(self):
        payload = json.dumps(
            {
                "title": "Submit first paper",
                "area": "research",
                "outcome": "Manuscript submitted",
                "target_date": "2027-03-01",
                "weekly_target_minutes": 600,
                "priority": "high",
            }
        )

        goal = json.loads(handle_plan_command(f"goal-create '{payload}'"))

        self.assertEqual(goal["area"], "research")
        self.assertEqual(goal["weekly_target_minutes"], 600)
        self.assertEqual(goal["priority"], "high")

    def test_desktop_rich_source_command_preserves_scan_bounds(self):
        payload = json.dumps(
            {
                "name": "Graduate school",
                "query": "from:grad-school.example newer_than:30d",
                "importance": "high",
                "max_results": 25,
            }
        )

        source = json.loads(handle_plan_command(f"source-create '{payload}'"))

        self.assertEqual(source["importance"], "high")
        self.assertEqual(source["max_results"], 25)
        self.assertTrue(source["enabled"])

    def test_capture_deduplicates_equivalent_content(self):
        first = self.service.capture(raw_content="  Registration   notice  ")
        second = self.service.capture(raw_content="registration notice")

        self.assertEqual(first["capture_outcome"], "created")
        self.assertEqual(second["capture_outcome"], "duplicate")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.service.list()), 1)

    def test_same_source_uri_creates_revision_and_preserves_omitted_dates(self):
        item = self.service.capture(
            raw_content="Registration closes September 18",
            title="Registration",
            due_at="2026-09-18T17:00:00+08:00",
            importance="high",
            source_type="web",
            source_uri="https://school.example/notice/1#details",
        )
        self.service.confirm(item["id"])

        updated = self.service.capture(
            raw_content="Registration instructions were updated",
            title="Registration update",
            source_type="email",
            source_uri="https://school.example/notice/1",
        )

        self.assertEqual(updated["capture_outcome"], "updated")
        self.assertEqual(updated["id"], item["id"])
        self.assertEqual(updated["status"], "draft")
        self.assertEqual(updated["due_at"], item["due_at"])
        self.assertEqual(updated["importance"], "high")
        revisions = self.service.revisions(item["id"])
        self.assertEqual(len(revisions), 1)
        self.assertIn("raw_content", revisions[0]["changes"])
        self.assertTrue(
            all(
                nudge["status"] != "pending"
                for nudge in self.store.list_nudges(item["id"])
            )
        )

    def test_confirm_requires_a_meaningful_time(self):
        item = self.service.capture(raw_content="未来可能有一次活动")
        with self.assertRaises(PlannerError):
            self.service.confirm(item["id"])

    def test_dispatch_claims_each_nudge_once(self):
        item = self._draft(actionable_at="2026-07-15T08:00:00+08:00")
        self.service.confirm(item["id"])

        first = self.service.dispatch_due(now=NOW)
        second = self.service.dispatch_due(now=NOW)
        self.assertIn(item["id"], first)
        self.assertEqual(second, "")

    def test_ack_does_not_cancel_future_nudges_but_done_does(self):
        item = self._draft(actionable_at="2026-07-15T08:00:00+08:00")
        self.service.confirm(item["id"])
        self.service.dispatch_due(now=NOW)
        acknowledged = self.service.acknowledge(item["id"])
        self.assertIsNotNone(acknowledged["acknowledged_at"])
        self.assertTrue(
            any(n["status"] == "pending" for n in self.store.list_nudges(item["id"]))
        )

        completed = self.service.complete(item["id"])
        self.assertEqual(completed["status"], "done")
        self.assertFalse(
            any(n["status"] == "pending" for n in self.store.list_nudges(item["id"]))
        )

    def test_cancel_item_reclaims_reminders_without_marking_work_done(self):
        item = self._draft(actionable_at="2026-07-15T08:00:00+08:00")
        self.service.confirm(item["id"])

        cancelled = self.service.cancel(item["id"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(cancelled["completed_at"])
        self.assertFalse(
            any(n["status"] == "pending" for n in self.store.list_nudges(item["id"]))
        )

    def test_snooze_cancels_only_nudges_through_snooze_time(self):
        item = self._draft(actionable_at="2026-07-15T08:00:00+08:00")
        self.service.confirm(item["id"])
        self.service.snooze(item["id"], "2026-07-16T09:00:00+08:00")
        nudges = self.store.list_nudges(item["id"])
        self.assertTrue(any(n["kind"] == "snooze" and n["status"] == "pending" for n in nudges))
        self.assertTrue(any(n["fire_at"] > "2026-07-16" and n["status"] == "pending" for n in nudges))

    def test_tool_capture_never_auto_confirms(self):
        with patch("plugins.personal_planner.tool.PlannerService", return_value=self.service):
            payload = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "capture",
                        "raw_content": "报名通知",
                        "due_at": "2026-09-18T17:00:00+08:00",
                    }
                )
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "draft")

    def test_tool_exposes_goal_task_and_schedule_drafts(self):
        with patch("plugins.personal_planner.tool.PlannerService", return_value=self.service):
            goal_payload = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "goal_create",
                        "title": "Internship search",
                        "area": "career",
                        "weekly_target_minutes": 300,
                    }
                )
            )
            self.assertTrue(goal_payload["ok"])
            goal_id = goal_payload["result"]["id"]

            availability_payload = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "availability_add",
                        "weekday": "wed",
                        "start_time": "08:00",
                        "end_time": "10:00",
                        "energy": "medium",
                    }
                )
            )
            self.assertTrue(availability_payload["ok"])

            task_payload = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "task_create",
                        "title": "Update resume",
                        "goal_id": goal_id,
                        "estimated_minutes": 60,
                    }
                )
            )
            self.assertTrue(task_payload["ok"])

            plan_payload = json.loads(
                handle_personal_planner_tool(
                    {"action": "schedule_preview", "schedule_days": 1}
                )
            )
            self.assertTrue(plan_payload["ok"])
            self.assertEqual(plan_payload["result"]["status"], "draft")
            self.assertTrue(
                plan_payload["result"]["requires_explicit_confirmation"]
            )

    def test_tool_exposes_block_checkin_and_plan_calendar_preview(self):
        _, plan = self._confirmed_plan()
        block = plan["blocks"][0]
        actions = PERSONAL_PLANNER_SCHEMA["parameters"]["properties"]["action"][
            "enum"
        ]
        self.assertIn("block_checkin", actions)
        self.assertIn("schedule_calendar_sync", actions)

        with patch(
            "plugins.personal_planner.tool.PlannerService",
            return_value=self.service,
        ):
            preview = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "schedule_calendar_preview",
                        "plan_id": plan["id"],
                    }
                )
            )
            shown = json.loads(
                handle_personal_planner_tool(
                    {"action": "block_get", "block_id": block["id"]}
                )
            )
            checked = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "block_checkin",
                        "block_id": block["id"],
                        "block_outcome": "done",
                        "actual_minutes": 25,
                    }
                )
            )

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["result"]["events"][0]["block_id"], block["id"])
        self.assertTrue(shown["ok"])
        self.assertTrue(checked["ok"])
        self.assertEqual(
            checked["result"]["block"]["checkin"]["credited_minutes"], 25
        )

    def test_milestone_actions_are_discoverable_and_confirmation_is_explicit(self):
        actions = PERSONAL_PLANNER_SCHEMA["parameters"]["properties"]["action"][
            "enum"
        ]
        for action in (
            "expert_analyze",
            "expert_prepare",
            "expert_ingest",
            "milestone_preview",
            "milestone_confirm",
            "milestone_reject",
            "milestone_checkin",
            "milestone_snooze",
            "milestone_overview",
        ):
            self.assertIn(action, actions)

        preview = self.store.create_milestone_plan_draft(
            window_start="2026-07-20T00:00:00Z",
            window_end="2026-07-27T00:00:00Z",
            timezone="Asia/Shanghai",
            summary={},
            milestones=[],
            now="2026-07-20T00:00:00Z",
        )
        denied = json.loads(
            handle_personal_planner_tool(
                {
                    "action": "milestone_confirm",
                    "plan_id": preview["id"],
                    "milestone_confirmed": False,
                },
                service=self.service,
            )
        )
        self.assertIn("explicit approval", denied["error"])

    def test_plan_command_supports_reject_and_partial_checkin(self):
        self.service.save_work_package(
            {
                "domain": "research",
                "objective": "Run experiment",
                "source_event_ids": ["EV-FIXTURE"],
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
        )
        for weekday in range(7):
            self.service.add_availability(
                weekday=weekday, start_time="08:00", end_time="20:00"
            )

        output = handle_plan_command(
            "milestone-preview 7", service=self.service
        )
        plan_id = output.split("[", 1)[1].split("]", 1)[0]
        confirmed = handle_plan_command(
            f"milestone-confirm {plan_id} confirm", service=self.service
        )
        self.assertIn("Confirmed", confirmed)
        shown = json.loads(
            handle_plan_command(f"milestone-show {plan_id}", service=self.service)
        )
        milestone_id = shown["milestones"][0]["id"]
        partial = handle_plan_command(
            f"milestone-checkin {milestone_id} partial 40 0.5",
            service=self.service,
        )
        self.assertIn("partial", partial.lower())
        replan_id = self.store.list_milestone_plans(status="draft", limit=1)[0][
            "id"
        ]
        rejected = handle_plan_command(
            f"milestone-reject {replan_id}", service=self.service
        )
        self.assertIn("Rejected", rejected)

    def test_native_cli_registers_milestone_state_operations(self):
        import argparse

        from plugins.personal_planner.cli import register_cli

        parser = argparse.ArgumentParser()
        register_cli(parser)

        preview = parser.parse_args(["milestone-preview", "--days", "7"])
        self.assertEqual(preview.planner_action, "milestone-preview")
        self.assertEqual(preview.days, 7)
        checkin = parser.parse_args(
            [
                "milestone-checkin",
                "DM-1",
                "--outcome",
                "partial",
                "--minutes",
                "40",
                "--fraction",
                "0.5",
            ]
        )
        self.assertEqual(checkin.outcome, "partial")
        confirm = parser.parse_args(
            ["milestone-confirm", "MP-2", "--confirm", "--replace", "MP-1"]
        )
        self.assertTrue(confirm.confirm)
        self.assertEqual(confirm.replace, "MP-1")

    def test_dispatch_script_passes_its_cron_delivery_state(self):
        from plugins.personal_planner.cron_support import (
            _review_script_content,
            _script_content,
        )

        script = _script_content(Path("D:/plugin/personal_planner"))

        self.assertIn("cron.list_jobs(include_disabled=True)", script)
        self.assertIn("Personal planner dispatch", script)
        self.assertIn("cron_job_state=job", script)
        self.assertIn("delivery_target=", script)

        review_script = _review_script_content(Path("D:/plugin/personal_planner"))
        self.assertIn("checkin.review_due", review_script)
        self.assertIn("today_milestones", review_script)

    def test_tool_exposes_gmail_source_management(self):
        actions = PERSONAL_PLANNER_SCHEMA["parameters"]["properties"]["action"][
            "enum"
        ]
        self.assertIn("source_add", actions)
        self.assertIn("source_scan", actions)

        with patch(
            "plugins.personal_planner.tool.PlannerService",
            return_value=self.service,
        ):
            created = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "source_add",
                        "source_name": "School",
                        "gmail_query": "from:example.edu newer_than:30d",
                        "importance": "high",
                    }
                )
            )
            listed = json.loads(
                handle_personal_planner_tool({"action": "source_list"})
            )
            disabled = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "source_status",
                        "source_id": created["result"]["id"],
                        "source_enabled": False,
                    }
                )
            )

        self.assertTrue(created["ok"])
        self.assertEqual(listed["result"][0]["name"], "School")
        self.assertFalse(disabled["result"]["enabled"])

    def test_profile_fact_requires_confirmation_before_monitor_proposal(self):
        fact = self.service.add_profile_fact(
            category="education",
            fact_key="school",
            fact_value="北京邮电大学",
        )

        before = self.service.generate_monitor_proposals()
        confirmed = self.service.confirm_profile_fact(fact["id"])
        after = self.service.generate_monitor_proposals()

        self.assertEqual(fact["status"], "proposed")
        self.assertEqual(before["proposals"], [])
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(
            after["proposals"][0]["topic_key"], "institution_notices"
        )
        self.assertFalse(after["network_scan_started"])
        self.assertTrue(after["requires_explicit_approval"])

    def test_profile_fact_replacement_supersedes_old_value(self):
        old = self._confirmed_fact(
            "education",
            "school",
            "Old University",
        )
        new = self.service.add_profile_fact(
            category="education",
            fact_key="school",
            fact_value="北京邮电大学",
        )
        self.service.confirm_profile_fact(new["id"])

        refreshed_old = self.service.get_profile_fact(old["id"])
        refreshed_new = self.service.get_profile_fact(new["id"])

        self.assertEqual(refreshed_old["status"], "superseded")
        self.assertEqual(refreshed_new["status"], "confirmed")

    def test_sensitive_and_expired_profile_facts_do_not_drive_radar(self):
        self._confirmed_fact(
            "education",
            "school",
            "Private University",
            privacy="sensitive",
        )
        self._confirmed_fact(
            "education",
            "degree",
            "硕士研究生",
            valid_until="2026-07-14T23:59:00+08:00",
        )

        result = self.service.generate_monitor_proposals()

        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["sensitive_fact_count_excluded"], 1)

    def test_bupt_computing_profile_and_goals_generate_explainable_radar(self):
        school = self._confirmed_fact(
            "education",
            "school",
            "北京邮电大学",
        )
        degree = self._confirmed_fact(
            "education",
            "degree",
            "硕士研究生",
        )
        field = self._confirmed_fact(
            "education",
            "field",
            "计算机",
        )
        self._confirmed_fact(
            "education",
            "graduation_year",
            "2028",
        )
        internship = self.service.create_goal(
            title="找到 AI 暑期实习",
            area="career",
        )
        paper = self.service.create_goal(
            title="发表一篇论文",
            area="research",
        )
        cet = self.service.create_goal(
            title="通过英语六级",
            area="coursework",
        )

        result = self.service.generate_monitor_proposals()
        topics = {
            value["topic_key"]: value for value in result["proposals"]
        }

        self.assertEqual(
            set(topics),
            {
                "institution_notices",
                "graduate_milestones",
                "computing_opportunities",
                "career_internship",
                "research_publication",
                "language_exams",
            },
        )
        self.assertIn(
            school["id"],
            {
                evidence["id"]
                for evidence in topics["institution_notices"]["evidence"]
            },
        )
        self.assertIn(
            degree["id"],
            {
                evidence["id"]
                for evidence in topics["graduate_milestones"]["evidence"]
            },
        )
        self.assertIn(
            field["id"],
            {
                evidence["id"]
                for evidence in topics["computing_opportunities"]["evidence"]
            },
        )
        self.assertIn(
            internship["id"],
            {
                evidence["id"]
                for evidence in topics["career_internship"]["evidence"]
            },
        )
        self.assertIn(
            paper["id"],
            {
                evidence["id"]
                for evidence in topics["research_publication"]["evidence"]
            },
        )
        self.assertIn(
            cet["id"],
            {
                evidence["id"]
                for evidence in topics["language_exams"]["evidence"]
            },
        )
        self.assertTrue(topics["career_internship"]["query_terms"])
        self.assertTrue(topics["career_internship"]["source_kinds"])

    def test_monitor_approval_is_idempotent_and_does_not_start_scan(self):
        self._confirmed_fact(
            "education",
            "school",
            "北京邮电大学",
        )
        generated = self.service.generate_monitor_proposals()
        proposal = generated["proposals"][0]

        approved = self.service.decide_monitor_proposal(
            proposal["id"],
            decision="approved",
        )
        repeated = self.service.decide_monitor_proposal(
            proposal["id"],
            decision="approved",
        )
        regenerated = self.service.generate_monitor_proposals()

        self.assertEqual(approved["status"], "approved")
        self.assertFalse(approved["network_scan_active"])
        self.assertEqual(repeated["id"], approved["id"])
        self.assertEqual(regenerated["proposals"][0]["status"], "approved")
        self.assertFalse(regenerated["network_scan_started"])

    def test_approved_monitor_can_be_rejected_to_stop_future_scans(self):
        self._confirmed_fact("education", "school", "北京邮电大学")
        proposal = self.service.generate_monitor_proposals()["proposals"][0]
        self.service.decide_monitor_proposal(proposal["id"], decision="approved")

        rejected = self.service.decide_monitor_proposal(
            proposal["id"], decision="rejected"
        )
        restored = self.service.decide_monitor_proposal(
            proposal["id"], decision="approved"
        )

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(restored["status"], "approved")

    def test_revoking_profile_fact_supersedes_approved_monitor(self):
        fact = self._confirmed_fact(
            "education",
            "school",
            "北京邮电大学",
        )
        proposal = self.service.generate_monitor_proposals()["proposals"][0]
        self.service.decide_monitor_proposal(
            proposal["id"],
            decision="approved",
        )

        self.service.revoke_profile_fact(fact["id"])
        regenerated = self.service.generate_monitor_proposals()
        old = self.service.get_monitor_proposal(proposal["id"])

        self.assertEqual(regenerated["proposals"], [])
        self.assertEqual(old["status"], "superseded")
        self.assertFalse(old["network_scan_active"])

    def test_tool_exposes_profile_and_monitor_approval_flow(self):
        actions = PERSONAL_PLANNER_SCHEMA["parameters"]["properties"]["action"][
            "enum"
        ]
        self.assertIn("profile_add", actions)
        self.assertIn("monitor_generate", actions)

        with patch(
            "plugins.personal_planner.tool.PlannerService",
            return_value=self.service,
        ):
            proposed = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "profile_add",
                        "profile_category": "education",
                        "profile_key": "school",
                        "profile_value": "北京邮电大学",
                    }
                )
            )
            confirmed = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "profile_confirm",
                        "fact_id": proposed["result"]["id"],
                    }
                )
            )
            generated = json.loads(
                handle_personal_planner_tool(
                    {"action": "monitor_generate"}
                )
            )
            approved = json.loads(
                handle_personal_planner_tool(
                    {
                        "action": "monitor_approve",
                        "monitor_id": generated["result"]["proposals"][0]["id"],
                    }
                )
            )

        self.assertTrue(proposed["ok"])
        self.assertEqual(confirmed["result"]["status"], "confirmed")
        self.assertFalse(generated["result"]["network_scan_started"])
        self.assertEqual(approved["result"]["status"], "approved")
        self.assertTrue(approved["result"]["network_scan_active"])
        self.assertTrue(approved["result"]["automation_job_id"])

    def test_gmail_source_scan_creates_review_only_draft_and_is_idempotent(self):
        source = self.service.add_gmail_source(
            name="Graduate school",
            query="from:example.edu newer_than:30d",
            importance="high",
            max_results=50,
        )
        gmail = FakeGmail([self._gmail_message()])

        first = self.service.scan_ingest_sources(client=gmail)
        second = self.service.scan_ingest_sources(client=gmail)
        item = self.service.get(first["captured"][0]["item_id"])

        self.assertEqual(first["new_draft_count"], 1)
        self.assertEqual(first["review_item_ids"], [item["id"]])
        self.assertTrue(first["requires_review"])
        self.assertFalse(first["auto_confirmed"])
        self.assertEqual(item["status"], "draft")
        self.assertIsNone(item["actionable_at"])
        self.assertIsNone(item["due_at"])
        self.assertIsNone(item["event_at"])
        self.assertEqual(item["source_type"], "gmail")
        self.assertEqual(second["captured"], [])
        self.assertEqual(second["sources"][0]["already_processed"], 1)
        self.assertEqual(
            len(self.store.list_ingest_receipts(source_id=source["id"])),
            1,
        )
        with self.assertRaises(PlannerError):
            self.service.confirm(item["id"])

    def test_gmail_followup_in_same_thread_reopens_confirmed_item_as_draft(self):
        source = self.service.add_gmail_source(
            name="School updates",
            query="from:example.edu newer_than:30d",
        )
        first_message = self._gmail_message()
        gmail = FakeGmail([first_message])
        first_scan = self.service.scan_ingest_sources(client=gmail)
        item_id = first_scan["captured"][0]["item_id"]
        current = self.service.get(item_id)
        prepared = self.service.capture(
            raw_content=current["raw_content"] + "\nReviewed dates.",
            title=current["title"],
            due_at="2026-09-18T17:00:00+08:00",
            source_type="gmail",
            source_uri=current["source_uri"],
        )
        self.service.confirm(prepared["id"])

        gmail.messages["msg-2"] = self._gmail_message(
            "msg-2",
            subject="CET-6 registration deadline changed",
            date="Thu, 16 Jul 2026 09:00:00 +0800",
            body="The registration deadline has changed. Review the new notice.",
        )
        followup = self.service.scan_ingest_sources(
            source_id=source["id"],
            client=gmail,
        )
        revised = self.service.get(item_id)

        self.assertEqual(followup["captured"][0]["item_id"], item_id)
        self.assertEqual(
            followup["captured"][0]["capture_outcome"], "updated"
        )
        self.assertEqual(revised["status"], "draft")
        self.assertEqual(revised["due_at"], prepared["due_at"])
        self.assertTrue(
            all(
                value["status"] != "pending"
                for value in self.store.list_nudges(item_id)
            )
        )
        self.assertEqual(
            len(self.store.list_ingest_receipts(source_id=source["id"])),
            2,
        )

    def test_gmail_scan_isolates_message_errors_and_retries_them(self):
        source = self.service.add_gmail_source(
            name="School inbox",
            query="newer_than:30d",
        )
        gmail = FakeGmail(
            [
                self._gmail_message("msg-1"),
                self._gmail_message(
                    "msg-2",
                    threadId="thread-2",
                    subject="Internship notice",
                ),
            ]
        )
        gmail.fail_get.add("msg-2")

        partial = self.service.scan_ingest_sources(client=gmail)
        gmail.fail_get.clear()
        retry = self.service.scan_ingest_sources(client=gmail)

        self.assertEqual(len(partial["captured"]), 1)
        self.assertEqual(len(partial["errors"]), 1)
        self.assertEqual(len(retry["captured"]), 1)
        self.assertEqual(retry["captured"][0]["message_id"], "msg-2")
        self.assertEqual(
            len(self.store.list_ingest_receipts(source_id=source["id"])),
            2,
        )

    def test_disabled_gmail_source_is_skipped_by_automatic_scan(self):
        source = self.service.add_gmail_source(
            name="Paused source",
            query="newer_than:30d",
        )
        self.service.set_ingest_source_enabled(source["id"], enabled=False)
        gmail = FakeGmail([self._gmail_message()])

        automatic = self.service.scan_ingest_sources(client=gmail)
        manual = self.service.scan_ingest_sources(
            source_id=source["id"],
            client=gmail,
        )

        self.assertEqual(automatic["sources"], [])
        self.assertEqual(len(gmail.search_calls), 1)
        self.assertEqual(manual["new_draft_count"], 1)

    def test_ingest_message_is_quiet_without_changes(self):
        self.assertEqual(
            render_ingest_message({"captured": [], "errors": []}),
            "",
        )
        message = render_ingest_message(
            {
                "captured": [
                    {
                        "item_id": "P-EXAMPLE",
                        "title": "Registration",
                        "item_status": "draft",
                    }
                ],
                "errors": [],
            }
        )
        self.assertIn("P-EXAMPLE", message)
        self.assertIn("review", message.lower())

    def test_calendar_preview_and_sync_are_explicit_and_idempotent(self):
        item = self._draft()
        self.service.confirm(item["id"])

        preview = self.service.calendar_preview(item["id"], kinds=["event"])
        self.assertTrue(preview["requires_explicit_confirmation"])
        self.assertEqual(preview["events"][0]["sync_status"], "ready")
        self.assertTrue(preview["events"][0]["start"].endswith("+08:00"))

        with self.assertRaises(PlannerError):
            self.service.calendar_sync(
                item["id"], confirmed=False, kinds=["event"], client=FakeCalendar()
            )

        calendar = FakeCalendar()
        synced = self.service.calendar_sync(
            item["id"], confirmed=True, kinds=["event"], client=calendar
        )
        self.assertEqual(len(calendar.calls), 1)
        self.assertEqual(len(synced["created"]), 1)
        self.assertEqual(synced["events"][0]["sync_status"], "linked")

        synced_again = self.service.calendar_sync(
            item["id"], confirmed=True, kinds=["event"], client=calendar
        )
        self.assertEqual(len(calendar.calls), 1)
        self.assertEqual(synced_again["created"], [])

    def test_google_gmail_adapter_uses_existing_workspace_helper_read_only(self):
        helper = Path(self.temp.name) / "google_api.py"
        helper.write_text("# helper", encoding="utf-8")
        adapter = GoogleWorkspaceGmail(script_path=helper)
        search_result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "id": "msg-1",
                        "threadId": "thread-1",
                        "subject": "School notice",
                    }
                ]
            ),
            stderr="",
        )
        get_result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(self._gmail_message()),
            stderr="",
        )
        with patch(
            "plugins.personal_planner.gmail_ingest.subprocess.run",
            side_effect=[search_result, get_result],
        ) as run:
            messages = adapter.search_messages(
                query="from:example.edu",
                max_results=25,
            )
            message = adapter.get_message(message_id="msg-1")

        search_command = run.call_args_list[0].args[0]
        get_command = run.call_args_list[1].args[0]
        self.assertEqual(search_command[2:4], ["gmail", "search"])
        self.assertIn("--max", search_command)
        self.assertEqual(get_command[2:4], ["gmail", "get"])
        self.assertNotIn("modify", search_command + get_command)
        self.assertEqual(messages[0]["id"], "msg-1")
        self.assertEqual(message["body"], self._gmail_message()["body"])

    def test_google_calendar_adapter_uses_existing_workspace_helper(self):
        helper = Path(self.temp.name) / "google_api.py"
        helper.write_text("# helper", encoding="utf-8")
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "created",
                    "id": "google-1",
                    "summary": "Example",
                    "htmlLink": "https://calendar.google/example",
                }
            ),
            stderr="",
        )
        adapter = GoogleWorkspaceCalendar(script_path=helper)
        with patch(
            "plugins.personal_planner.calendar_sync.subprocess.run",
            return_value=completed,
        ) as run:
            result = adapter.create_event(
                calendar_id="primary",
                summary="Example",
                start="2026-09-10T09:00:00+08:00",
                end="2026-09-10T09:15:00+08:00",
                description="Planner event",
            )
        command = run.call_args.args[0]
        self.assertEqual(command[1], str(helper))
        self.assertEqual(command[2:4], ["calendar", "create"])
        self.assertIn("--description", command)
        self.assertEqual(result["id"], "google-1")

    def test_google_calendar_adapter_lists_busy_events_read_only(self):
        helper = Path(self.temp.name) / "google_api.py"
        helper.write_text("# helper", encoding="utf-8")
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "id": "class-1",
                        "summary": "Machine learning",
                        "start": "2026-07-15T09:00:00+08:00",
                        "end": "2026-07-15T11:00:00+08:00",
                        "status": "confirmed",
                    }
                ]
            ),
            stderr="",
        )
        adapter = GoogleWorkspaceCalendar(script_path=helper)
        with patch(
            "plugins.personal_planner.calendar_sync.subprocess.run",
            return_value=completed,
        ) as run:
            result = adapter.list_events(
                calendar_id="primary",
                start="2026-07-15T00:00:00Z",
                end="2026-07-16T00:00:00Z",
            )
        command = run.call_args.args[0]
        self.assertEqual(command[2:4], ["calendar", "list"])
        self.assertIn("--start", command)
        self.assertIn("--end", command)
        self.assertEqual(result[0]["id"], "class-1")

    def test_notification_update_marks_calendar_link_stale(self):
        item = self.service.capture(
            raw_content="Exam is December 14",
            event_at="2026-12-14T09:00:00+08:00",
            source_uri="https://school.example/exam",
        )
        self.service.confirm(item["id"])
        self.service.calendar_sync(
            item["id"], confirmed=True, kinds=["event"], client=FakeCalendar()
        )

        updated = self.service.capture(
            raw_content="Exam moved to December 21",
            event_at="2026-12-21T09:00:00+08:00",
            source_uri="https://school.example/exam",
        )
        self.assertEqual(updated["status"], "draft")
        self.service.confirm(updated["id"])
        preview = self.service.calendar_preview(updated["id"], kinds=["event"])
        self.assertEqual(
            preview["events"][0]["sync_status"], "manual_update_required"
        )
        with self.assertRaises(PlannerError):
            self.service.calendar_sync(
                updated["id"],
                confirmed=True,
                kinds=["event"],
                client=FakeCalendar(),
            )

        forgotten = self.service.calendar_forget(
            updated["id"],
            confirmed_external_cleanup=True,
            kinds=["event"],
        )
        self.assertEqual(len(forgotten["removed_links"]), 1)
        retry = self.service.calendar_sync(
            updated["id"], confirmed=True, kinds=["event"], client=FakeCalendar()
        )
        self.assertEqual(len(retry["created"]), 1)

    def test_failed_calendar_write_is_not_automatically_retried(self):
        item = self._draft()
        self.service.confirm(item["id"])
        with self.assertRaises(RuntimeError):
            self.service.calendar_sync(
                item["id"],
                confirmed=True,
                kinds=["event"],
                client=FailingCalendar(),
            )
        preview = self.service.calendar_preview(item["id"], kinds=["event"])
        self.assertEqual(preview["events"][0]["sync_status"], "previous_sync_failed")
        with self.assertRaises(PlannerError):
            self.service.calendar_sync(
                item["id"],
                confirmed=True,
                kinds=["event"],
                client=FakeCalendar(),
            )

    def test_goal_and_task_can_model_long_term_work(self):
        goal = self.service.create_goal(
            title="Submit first paper",
            area="research",
            outcome="Manuscript submitted",
            target_date="2027-03-01",
            weekly_target_minutes=600,
            priority="high",
        )
        task = self.service.create_task(
            title="Literature review",
            goal_id=goal["id"],
            estimated_minutes=240,
            min_block_minutes=60,
            energy="high",
            due_at="2026-07-25T20:00:00+08:00",
        )

        self.assertEqual(goal["status"], "active")
        self.assertEqual(goal["weekly_target_minutes"], 600)
        self.assertEqual(task["goal_id"], goal["id"])
        self.assertEqual(self.service.get_task(task["id"])["remaining_minutes"], 240)

    def test_backlog_task_can_be_edited_and_cancelled(self):
        goal = self.service.create_goal(title="Submit first paper", area="research")
        task = self.service.create_task(
            title="Draft outline",
            estimated_minutes=60,
            min_block_minutes=30,
        )

        edited = self.service.update_task(
            task["id"],
            {
                "title": "Draft paper outline",
                "estimated_minutes": 120,
                "min_block_minutes": 45,
                "energy": "high",
                "priority": "high",
                "goal_id": goal["id"],
            },
        )
        cancelled = self.service.cancel_task(task["id"])

        self.assertEqual(edited["title"], "Draft paper outline")
        self.assertEqual(edited["remaining_minutes"], 120)
        self.assertEqual(edited["energy"], "high")
        self.assertEqual(edited["goal_id"], goal["id"])
        self.assertEqual(cancelled["status"], "cancelled")

    def test_planned_task_requires_plan_cancellation_before_edit(self):
        task, plan = self._confirmed_plan()

        with self.assertRaises(PlannerError):
            self.service.update_task(task["id"], {"estimated_minutes": 90})

        cancelled_plan = self.service.cancel_schedule(plan["id"])
        edited = self.service.update_task(task["id"], {"estimated_minutes": 90})

        self.assertEqual(cancelled_plan["status"], "cancelled")
        self.assertTrue(all(block["status"] == "skipped" for block in cancelled_plan["blocks"]))
        self.assertEqual(edited["status"], "backlog")
        self.assertEqual(edited["remaining_minutes"], 90)

    def test_task_linked_to_notification_inherits_title_deadline_and_priority(self):
        item = self._draft()
        task = self.service.create_task(
            planner_item_id=item["id"],
            estimated_minutes=90,
        )

        self.assertEqual(task["title"], item["title"])
        self.assertEqual(task["due_at"], item["due_at"])
        self.assertEqual(task["priority"], "high")

    def test_availability_rejects_overlaps_but_allows_adjacent_windows(self):
        first = self.service.add_availability(
            weekday="mon",
            start_time="19:00",
            end_time="21:00",
            energy="high",
        )
        with self.assertRaises(PlannerError):
            self.service.add_availability(
                weekday="monday",
                start_time="20:00",
                end_time="22:00",
            )
        adjacent = self.service.add_availability(
            weekday=0,
            start_time="21:00",
            end_time="22:00",
            energy="medium",
        )

        self.assertNotEqual(first["id"], adjacent["id"])
        self.assertEqual(len(self.service.list_availability(weekday="mon")), 2)

    def test_availability_can_end_at_midnight(self):
        self.service.add_availability(
            weekday="wed",
            start_time="22:00",
            end_time="24:00",
            energy="medium",
        )
        self.service.create_task(
            title="Evening reading",
            estimated_minutes=60,
        )

        plan = self.service.schedule_preview(days=1)

        self.assertTrue(plan["blocks"])
        self.assertEqual(plan["blocks"][0]["starts_at"], "2026-07-15T14:00:00Z")

    def test_schedule_respects_fixed_events_energy_and_capacity_buffer(self):
        event = self.service.capture(
            raw_content="Research seminar",
            event_at="2026-07-15T09:00:00+08:00",
            event_duration_minutes=60,
        )
        self.service.confirm(event["id"])
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="high",
            label="Deep work",
        )
        task = self.service.create_task(
            title="Write experiment section",
            estimated_minutes=180,
            min_block_minutes=30,
            energy="high",
            priority="high",
        )

        plan = self.service.schedule_preview(days=1)

        self.assertEqual(plan["status"], "draft")
        self.assertTrue(plan["requires_explicit_confirmation"])
        self.assertEqual(plan["summary"]["capacity"]["raw_available_minutes"], 180)
        self.assertEqual(plan["summary"]["capacity"]["usable_minutes"], 140)
        self.assertEqual(plan["summary"]["capacity"]["scheduled_minutes"], 140)
        self.assertTrue(all(block["task_id"] == task["id"] for block in plan["blocks"]))
        event_start = datetime.fromisoformat("2026-07-15T01:00:00+00:00")
        event_end = datetime.fromisoformat("2026-07-15T02:00:00+00:00")
        for block in plan["blocks"]:
            start = datetime.fromisoformat(block["starts_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(block["ends_at"].replace("Z", "+00:00"))
            self.assertTrue(end <= event_start or start >= event_end)

    def test_schedule_reads_google_calendar_busy_time_when_enabled(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="high",
        )
        self.service.create_task(
            title="Deep research",
            estimated_minutes=180,
            min_block_minutes=30,
            energy="high",
        )
        calendar = FakeCalendar(
            events=[
                {
                    "id": "class-1",
                    "summary": "Graduate class",
                    "start": "2026-07-15T09:00:00+08:00",
                    "end": "2026-07-15T11:00:00+08:00",
                    "status": "confirmed",
                }
            ]
        )

        plan = self.service.schedule_preview(
            days=1,
            use_calendar=True,
            calendar_client=calendar,
        )

        self.assertEqual(plan["summary"]["external_calendar"]["status"], "live")
        self.assertEqual(plan["summary"]["external_calendar"]["event_count"], 1)
        self.assertEqual(len(calendar.list_calls), 1)
        busy_start = datetime.fromisoformat("2026-07-15T01:00:00+00:00")
        busy_end = datetime.fromisoformat("2026-07-15T03:00:00+00:00")
        for block in plan["blocks"]:
            start = datetime.fromisoformat(block["starts_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(block["ends_at"].replace("Z", "+00:00"))
            self.assertTrue(end <= busy_start or start >= busy_end)

    def test_calendar_busy_failure_uses_cache_and_surfaces_warning(self):
        calendar = FakeCalendar(
            events=[
                {
                    "id": "meeting-1",
                    "summary": "Advisor meeting",
                    "start": "2026-07-15T10:00:00+08:00",
                    "end": "2026-07-15T11:00:00+08:00",
                    "status": "confirmed",
                }
            ]
        )
        live = self.service.calendar_busy(
            start_at="2026-07-15T08:00:00+08:00",
            end_at="2026-07-15T12:00:00+08:00",
            client=calendar,
        )
        cached = self.service.calendar_busy(
            start_at="2026-07-15T08:00:00+08:00",
            end_at="2026-07-15T12:00:00+08:00",
            client=FailingCalendar(),
        )

        self.assertEqual(live["summary"]["status"], "live")
        self.assertEqual(cached["summary"]["status"], "cached")
        self.assertEqual(cached["events"][0]["event_id"], "meeting-1")
        self.assertTrue(cached["summary"]["warnings"])

    def test_empty_calendar_result_is_cached_explicitly(self):
        live = self.service.calendar_busy(
            start_at="2026-07-15T08:00:00+08:00",
            end_at="2026-07-15T12:00:00+08:00",
            client=FakeCalendar(events=[]),
        )
        cached = self.service.calendar_busy(
            start_at="2026-07-15T08:00:00+08:00",
            end_at="2026-07-15T12:00:00+08:00",
            client=FailingCalendar(),
        )

        self.assertEqual(live["summary"]["status"], "live")
        self.assertEqual(live["summary"]["event_count"], 0)
        self.assertEqual(cached["summary"]["status"], "cached")
        self.assertEqual(cached["summary"]["event_count"], 0)
        self.assertEqual(cached["events"], [])
        self.assertTrue(cached["summary"]["warnings"])

    def test_calendar_busy_failure_without_cache_does_not_break_local_plan(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="10:00",
            energy="medium",
        )
        self.service.create_task(title="Local fallback task", estimated_minutes=60)

        plan = self.service.schedule_preview(
            days=1,
            use_calendar=True,
            calendar_client=FailingCalendar(),
        )

        external = plan["summary"]["external_calendar"]
        self.assertEqual(external["status"], "unavailable")
        self.assertTrue(external["warnings"])
        self.assertTrue(plan["blocks"])

    def test_schedule_exposes_energy_mismatch_instead_of_dropping_task(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="10:00",
            energy="low",
        )
        task = self.service.create_task(
            title="Hard theorem proof",
            estimated_minutes=60,
            energy="high",
        )

        plan = self.service.schedule_preview(days=1)

        self.assertEqual(plan["blocks"], [])
        self.assertEqual(plan["summary"]["unscheduled"][0]["task_id"], task["id"])
        self.assertEqual(
            plan["summary"]["unscheduled"][0]["reason"],
            "no_compatible_energy_window",
        )

    def test_schedule_confirmation_is_explicit_and_marks_tasks_planned(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="10:00",
            energy="medium",
        )
        task = self.service.create_task(
            title="Prepare course presentation",
            estimated_minutes=60,
        )
        plan = self.service.schedule_preview(days=1)

        with self.assertRaises(PlannerError):
            self.service.confirm_schedule(plan["id"], confirmed=False)
        self.assertEqual(self.service.get_task(task["id"])["status"], "backlog")

        confirmed = self.service.confirm_schedule(plan["id"], confirmed=True)
        confirmed_again = self.service.confirm_schedule(plan["id"], confirmed=True)
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed_again["status"], "confirmed")
        self.assertEqual(self.service.get_task(task["id"])["status"], "planned")
        self.assertTrue(
            all(block["status"] == "confirmed" for block in confirmed["blocks"])
        )

    def test_block_done_records_actual_progress_and_is_idempotent(self):
        task, plan = self._confirmed_plan(estimated_minutes=90)
        block = plan["blocks"][0]

        checked = self.service.checkin_block(
            block["id"],
            outcome="done",
            actual_minutes=35,
            note="Completed the first section",
        )
        repeated = self.service.checkin_block(
            block["id"],
            outcome="done",
            actual_minutes=35,
        )

        self.assertEqual(checked["block"]["status"], "done")
        self.assertEqual(checked["block"]["checkin"]["actual_minutes"], 35)
        self.assertEqual(checked["block"]["checkin"]["credited_minutes"], 35)
        self.assertEqual(repeated["block"]["checkin"]["id"], checked["block"]["checkin"]["id"])
        self.assertEqual(self.service.get_task(task["id"])["completed_minutes"], 35)
        with self.assertRaisesRegex(PlannerError, "already checked in"):
            self.service.checkin_block(block["id"], outcome="deferred")

    def test_block_done_caps_credit_and_releases_future_task_blocks(self):
        task, plan = self._confirmed_plan(estimated_minutes=180)
        self.assertGreater(len(plan["blocks"]), 1)

        checked = self.service.checkin_block(
            plan["blocks"][0]["id"],
            outcome="done",
            actual_minutes=300,
        )

        refreshed = self.service.get_schedule(plan["id"])
        self.assertEqual(checked["block"]["checkin"]["credited_minutes"], 180)
        self.assertEqual(self.service.get_task(task["id"])["status"], "done")
        self.assertTrue(
            all(block["status"] == "done" for block in refreshed["blocks"])
        )

    def test_block_defer_can_propose_replan_and_triggers_daily_review(self):
        _, plan = self._confirmed_plan()
        block = plan["blocks"][0]

        checked = self.service.checkin_block(
            block["id"],
            outcome="deferred",
            note="Unexpected meeting",
            propose_replan=True,
        )
        review = self.service.daily_review()

        self.assertEqual(checked["block"]["status"], "skipped")
        self.assertEqual(checked["block"]["checkin"]["outcome"], "deferred")
        self.assertEqual(checked["block"]["checkin"]["credited_minutes"], 0)
        self.assertEqual(checked["proposed_replan"]["status"], "draft")
        self.assertIn(
            plan["id"], checked["proposed_replan"]["replaces_plan_ids"]
        )
        self.assertEqual(self.service.get_schedule(plan["id"])["status"], "confirmed")
        self.assertEqual(review["deferred_blocks"][0]["block_id"], block["id"])
        self.assertEqual(review["planned_minutes"], block["minutes"])
        self.assertIn("deferred_blocks", review["recommendation_reasons"])

    def test_skipped_block_remains_in_planned_minutes_and_recommends_replan(self):
        _, plan = self._confirmed_plan()
        block = plan["blocks"][0]

        self.service.checkin_block(block["id"], outcome="skipped")
        review = self.service.daily_review()

        self.assertEqual(review["planned_minutes"], block["minutes"])
        self.assertEqual(review["skipped_blocks"][0]["block_id"], block["id"])
        self.assertTrue(review["replan_recommended"])
        self.assertIn("skipped_blocks", review["recommendation_reasons"])

    def test_invalid_done_replan_request_does_not_mutate_block(self):
        _, plan = self._confirmed_plan()
        block = plan["blocks"][0]

        with self.assertRaisesRegex(PlannerError, "only available"):
            self.service.checkin_block(
                block["id"],
                outcome="done",
                propose_replan=True,
            )

        unchanged = self.service.get_block(block["id"])
        self.assertEqual(unchanged["status"], "confirmed")
        self.assertIsNone(unchanged["checkin"])

    def test_confirmed_plan_calendar_sync_is_explicit_and_idempotent(self):
        _, plan = self._confirmed_plan()
        with self.assertRaises(PlannerError):
            self.service.schedule_calendar_sync(
                plan["id"],
                confirmed=False,
                client=FakeCalendar(),
            )

        calendar = FakeCalendar()
        preview = self.service.schedule_calendar_preview(plan["id"])
        synced = self.service.schedule_calendar_sync(
            plan["id"],
            confirmed=True,
            client=calendar,
        )
        repeated = self.service.schedule_calendar_sync(
            plan["id"],
            confirmed=True,
            client=calendar,
        )

        self.assertTrue(preview["requires_explicit_confirmation"])
        self.assertTrue(
            all(event["sync_status"] == "ready" for event in preview["events"])
        )
        self.assertEqual(len(calendar.calls), len(preview["events"]))
        self.assertEqual(len(synced["created"]), len(preview["events"]))
        self.assertTrue(
            all(event["sync_status"] == "linked" for event in synced["events"])
        )
        self.assertEqual(repeated["created"], [])
        self.assertEqual(len(calendar.calls), len(preview["events"]))

    def test_plan_calendar_sync_requires_a_persisted_matching_preview(self):
        _, plan = self._confirmed_plan()
        calendar = FakeCalendar()

        with self.assertRaisesRegex(PlannerError, "prior"):
            self.service.schedule_calendar_sync(
                plan["id"],
                confirmed=True,
                client=calendar,
            )
        self.assertEqual(calendar.calls, [])

        preview = self.service.schedule_calendar_preview(plan["id"])
        self.service.checkin_block(
            preview["events"][0]["block_id"],
            outcome="deferred",
        )
        with self.assertRaisesRegex(PlannerError, "changed after"):
            self.service.schedule_calendar_sync(
                plan["id"],
                confirmed=True,
                client=calendar,
            )
        self.assertEqual(calendar.calls, [])

    def test_deferred_calendar_block_requires_manual_reconciliation(self):
        _, plan = self._confirmed_plan()
        block = plan["blocks"][0]
        calendar = FakeCalendar()
        self.service.schedule_calendar_preview(plan["id"])
        self.service.schedule_calendar_sync(
            plan["id"],
            confirmed=True,
            client=calendar,
        )

        self.service.checkin_block(block["id"], outcome="deferred")
        preview = self.service.schedule_calendar_preview(plan["id"])

        self.assertEqual(preview["events"], [])
        self.assertEqual(preview["stale_links"][0]["block_id"], block["id"])
        with self.assertRaisesRegex(PlannerError, "manual reconciliation"):
            self.service.schedule_calendar_sync(
                plan["id"],
                confirmed=True,
                client=calendar,
            )
        with self.assertRaises(PlannerError):
            self.service.schedule_calendar_forget(
                plan["id"],
                confirmed_external_cleanup=False,
            )
        forgotten = self.service.schedule_calendar_forget(
            plan["id"],
            confirmed_external_cleanup=True,
        )
        self.assertEqual(len(forgotten["removed_links"]), 1)
        self.assertEqual(len(calendar.calls), 1)

    def test_replan_marks_old_plan_calendar_links_stale(self):
        _, plan = self._confirmed_plan(estimated_minutes=60)
        calendar = FakeCalendar()
        self.service.schedule_calendar_preview(plan["id"])
        self.service.schedule_calendar_sync(
            plan["id"],
            confirmed=True,
            client=calendar,
        )
        self.service.create_task(
            title="Urgent application",
            estimated_minutes=60,
            energy="high",
            priority="critical",
            due_at="2026-07-15T12:00:00+08:00",
        )

        replacement = self.service.schedule_preview(days=1, replan=True)
        self.service.confirm_schedule(
            replacement["id"],
            confirmed=True,
            replace=True,
        )
        old_preview = self.service.schedule_calendar_preview(plan["id"])

        self.assertEqual(old_preview["plan_status"], "superseded")
        self.assertTrue(old_preview["stale_links"])
        self.assertTrue(
            all(link["status"] == "stale" for link in old_preview["stale_links"])
        )
        self.assertEqual(len(calendar.calls), 1)

    def test_failed_plan_block_calendar_write_is_not_retried(self):
        _, plan = self._confirmed_plan()
        self.service.schedule_calendar_preview(plan["id"])
        with self.assertRaises(RuntimeError):
            self.service.schedule_calendar_sync(
                plan["id"],
                confirmed=True,
                client=FailingCalendar(),
            )

        preview = self.service.schedule_calendar_preview(plan["id"])
        self.assertEqual(
            preview["events"][0]["sync_status"], "previous_sync_failed"
        )
        with self.assertRaisesRegex(PlannerError, "manual reconciliation"):
            self.service.schedule_calendar_sync(
                plan["id"],
                confirmed=True,
                client=FakeCalendar(),
            )

    def test_block_change_during_calendar_write_records_external_link_stale(self):
        _, plan = self._confirmed_plan()
        block = plan["blocks"][0]
        self.service.schedule_calendar_preview(plan["id"])

        class ChangingCalendar:
            def __init__(self, service):
                self.service = service
                self.calls = 0

            def create_event(self, **values):
                self.calls += 1
                self.service.checkin_block(block["id"], outcome="deferred")
                return {
                    "id": "created-during-change",
                    "htmlLink": "https://calendar.example/changed",
                }

        calendar = ChangingCalendar(self.service)
        with self.assertRaisesRegex(PlannerError, "recorded as stale"):
            self.service.schedule_calendar_sync(
                plan["id"],
                confirmed=True,
                client=calendar,
            )

        refreshed = self.service.get_block(block["id"])
        self.assertEqual(calendar.calls, 1)
        self.assertEqual(
            refreshed["calendar_links"][0]["external_event_id"],
            "created-during-change",
        )
        self.assertEqual(refreshed["calendar_links"][0]["status"], "stale")

    def test_calendar_backed_schedule_is_revalidated_before_confirmation(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="high",
        )
        self.service.create_task(
            title="Write research notes",
            estimated_minutes=60,
            energy="high",
        )
        calendar = FakeCalendar(events=[])
        plan = self.service.schedule_preview(
            days=1,
            use_calendar=True,
            calendar_client=calendar,
        )

        confirmed = self.service.confirm_schedule(
            plan["id"],
            confirmed=True,
            calendar_client=calendar,
        )

        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(len(calendar.list_calls), 2)

    def test_calendar_change_rejects_old_schedule_draft(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="high",
        )
        self.service.create_task(
            title="Write research notes",
            estimated_minutes=60,
            energy="high",
        )
        calendar = FakeCalendar(events=[])
        plan = self.service.schedule_preview(
            days=1,
            use_calendar=True,
            calendar_client=calendar,
        )
        calendar.events.append(
            {
                "id": "new-meeting",
                "summary": "New meeting",
                "start": "2026-07-15T09:00:00+08:00",
                "end": "2026-07-15T10:00:00+08:00",
                "status": "confirmed",
            }
        )

        with self.assertRaisesRegex(PlannerError, "calendar changed"):
            self.service.confirm_schedule(
                plan["id"],
                confirmed=True,
                calendar_client=calendar,
            )
        self.assertEqual(self.service.get_schedule(plan["id"])["status"], "draft")

    def test_calendar_unavailable_rejects_calendar_backed_confirmation(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="high",
        )
        self.service.create_task(
            title="Write research notes",
            estimated_minutes=60,
            energy="high",
        )
        plan = self.service.schedule_preview(
            days=1,
            use_calendar=True,
            calendar_client=FakeCalendar(events=[]),
        )

        with self.assertRaisesRegex(PlannerError, "cannot validate calendar"):
            self.service.confirm_schedule(
                plan["id"],
                confirmed=True,
                calendar_client=FailingCalendar(),
            )

    def test_expired_schedule_draft_requires_fresh_preview(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="medium",
        )
        self.service.create_task(title="Read paper", estimated_minutes=60)
        plan = self.service.schedule_preview(days=1)
        later = PlannerService(
            store=self.store,
            settings=self.service.settings,
            now_fn=lambda: datetime(2026, 7, 15, 0, 31, tzinfo=timezone.utc),
        )

        with self.assertRaisesRegex(PlannerError, "draft expired"):
            later.confirm_schedule(plan["id"], confirmed=True)

    def test_replan_keeps_old_plan_until_replace_is_confirmed(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="11:00",
            energy="high",
        )
        self.service.create_task(
            title="Research baseline",
            estimated_minutes=60,
            energy="high",
        )
        first = self.service.schedule_preview(days=1)
        self.service.confirm_schedule(first["id"], confirmed=True)
        urgent = self.service.create_task(
            title="Urgent internship application",
            estimated_minutes=60,
            energy="high",
            priority="critical",
            due_at="2026-07-15T12:00:00+08:00",
        )

        replacement = self.service.schedule_preview(days=1, replan=True)

        self.assertIn(first["id"], replacement["replaces_plan_ids"])
        self.assertTrue(replacement["confirmation_requires_replace"])
        self.assertEqual(
            self.service.get_schedule(first["id"])["status"], "confirmed"
        )
        with self.assertRaises(PlannerError):
            self.service.confirm_schedule(replacement["id"], confirmed=True)

        confirmed = self.service.confirm_schedule(
            replacement["id"], confirmed=True, replace=True
        )
        self.assertEqual(confirmed["status"], "confirmed")
        old = self.service.get_schedule(first["id"])
        self.assertEqual(old["status"], "superseded")
        self.assertTrue(
            all(block["status"] == "superseded" for block in old["blocks"])
        )
        self.assertEqual(confirmed["blocks"][0]["task_id"], urgent["id"])

    def test_task_progress_controls_remaining_work(self):
        task = self.service.create_task(
            title="Write methods section",
            estimated_minutes=90,
        )
        progressed = self.service.add_task_progress(task["id"], 30)
        completed = self.service.add_task_progress(task["id"], 100)

        self.assertEqual(progressed["remaining_minutes"], 60)
        self.assertEqual(completed["completed_minutes"], 90)
        self.assertEqual(completed["remaining_minutes"], 0)
        self.assertEqual(completed["status"], "done")

    def test_daily_review_uses_progress_log_without_claiming_exact_block_completion(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="09:00",
            energy="medium",
        )
        task = self.service.create_task(
            title="Read paper",
            estimated_minutes=60,
        )
        plan = self.service.schedule_preview(days=1)
        self.service.confirm_schedule(plan["id"], confirmed=True)

        later = PlannerService(
            store=self.store,
            settings=self.service.settings,
            now_fn=lambda: datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc),
        )
        later.add_task_progress(task["id"], 10, note="Read introduction")
        review = later.daily_review()

        self.assertEqual(review["recorded_progress_minutes"], 10)
        self.assertEqual(review["overdue_unaccounted_minutes"], 35)
        self.assertTrue(review["replan_recommended"])
        self.assertIn("overdue_planned_work", review["recommendation_reasons"])
        self.assertEqual(review["progress_entries"][0]["note"], "Read introduction")

    def test_daily_review_detects_calendar_conflict_and_can_propose_replan(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="high",
        )
        task = self.service.create_task(
            title="Write paper",
            estimated_minutes=120,
            energy="high",
        )
        plan = self.service.schedule_preview(days=1)
        self.service.confirm_schedule(plan["id"], confirmed=True)
        first_block = self.service.get_schedule(plan["id"])["blocks"][0]
        conflict_calendar = FakeCalendar(
            events=[
                {
                    "id": "surprise-meeting",
                    "summary": "Advisor meeting",
                    "start": first_block["starts_at"],
                    "end": first_block["ends_at"],
                    "status": "confirmed",
                }
            ]
        )

        review = self.service.daily_review(
            use_calendar=True,
            calendar_client=conflict_calendar,
            propose_replan=True,
        )

        self.assertEqual(
            review["calendar_conflicts"][0]["task_id"], task["id"]
        )
        self.assertIn("calendar_conflicts", review["recommendation_reasons"])
        self.assertIsNotNone(review["proposed_replan"])
        self.assertIn(
            plan["id"], review["proposed_replan"]["replaces_plan_ids"]
        )
        self.assertEqual(
            self.service.get_schedule(plan["id"])["status"], "confirmed"
        )

    def test_daily_review_detects_uncovered_near_term_deadline(self):
        task = self.service.create_task(
            title="Submit application",
            estimated_minutes=90,
            due_at="2026-07-16T12:00:00+08:00",
            priority="critical",
        )

        review = self.service.daily_review()

        self.assertEqual(review["deadline_risks"][0]["task_id"], task["id"])
        self.assertEqual(review["deadline_risks"][0]["uncovered_minutes"], 90)
        self.assertIn(
            "deadline_capacity_gap", review["recommendation_reasons"]
        )

    def test_completed_task_invalidates_draft_and_releases_future_blocks(self):
        self.service.add_availability(
            weekday="wed",
            start_time="08:00",
            end_time="12:00",
            energy="medium",
        )
        stale_task = self.service.create_task(
            title="Already finished",
            estimated_minutes=60,
        )
        stale_plan = self.service.schedule_preview(days=1)
        self.service.complete_task(stale_task["id"])
        with self.assertRaises(PlannerError):
            self.service.confirm_schedule(stale_plan["id"], confirmed=True)

        active_task = self.service.create_task(
            title="Finish before its block",
            estimated_minutes=60,
        )
        active_plan = self.service.schedule_preview(days=1)
        self.service.confirm_schedule(active_plan["id"], confirmed=True)
        self.service.complete_task(active_task["id"])

        refreshed = self.service.get_schedule(active_plan["id"])
        self.assertTrue(
            all(block["status"] == "done" for block in refreshed["blocks"])
        )

    def test_schema_v1_migrates_without_losing_items(self):
        path = self.home / "planner" / "legacy.db"
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE planner_items (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                raw_content TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'manual',
                source_uri TEXT,
                discovered_at TEXT NOT NULL,
                actionable_at TEXT,
                due_at TEXT,
                event_at TEXT,
                timezone TEXT NOT NULL,
                importance TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'draft',
                acknowledged_at TEXT,
                completed_at TEXT,
                snoozed_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO planner_items VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "P-LEGACY",
                "Legacy",
                "Legacy notice",
                "manual",
                None,
                "2026-07-15T00:00:00Z",
                None,
                "2026-09-01T00:00:00Z",
                None,
                "Asia/Shanghai",
                "normal",
                "draft",
                None,
                None,
                None,
                "2026-07-15T00:00:00Z",
                "2026-07-15T00:00:00Z",
            ),
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        migrated = PlannerStore(path)
        item = migrated.get_item("P-LEGACY")
        self.assertIsNotNone(item)
        self.assertTrue(item["source_key"])
        self.assertTrue(item["content_hash"])
        self.assertEqual(item["event_duration_minutes"], 60)
        with migrated.read() as migrated_conn:
            self.assertEqual(
                migrated_conn.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION,
            )
            table_names = {
                row[0]
                for row in migrated_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("planner_goals", table_names)
            self.assertIn("planner_tasks", table_names)
            self.assertIn("planner_plan_runs", table_names)
            self.assertIn("planner_task_progress_log", table_names)
            self.assertIn("planner_calendar_busy_cache", table_names)
            self.assertIn("planner_calendar_sync_state", table_names)
            self.assertIn("planner_block_checkins", table_names)
            self.assertIn("planner_plan_block_calendar_links", table_names)
            self.assertIn("planner_plan_calendar_previews", table_names)
            self.assertIn("planner_ingest_sources", table_names)
            self.assertIn("planner_ingest_receipts", table_names)
            self.assertIn("planner_profile_facts", table_names)
            self.assertIn("planner_monitor_proposals", table_names)
            self.assertIn("planner_events", table_names)
            self.assertIn("planner_expert_results", table_names)
            self.assertIn("planner_work_packages", table_names)
            self.assertIn("planner_milestone_plan_runs", table_names)
            self.assertIn("planner_daily_milestones", table_names)
            self.assertIn("planner_milestone_checkins", table_names)
            self.assertIn("planner_agent_runs", table_names)

    def test_default_store_is_profile_scoped(self):
        first_home = Path(self.temp.name) / "profile-a"
        second_home = Path(self.temp.name) / "profile-b"

        first_token = set_hermes_home_override(first_home)
        try:
            first = PlannerService(settings=self.service.settings, now_fn=lambda: NOW)
            first.capture(raw_content="profile A notice")
            self.assertEqual(len(first.list()), 1)
        finally:
            reset_hermes_home_override(first_token)

        second_token = set_hermes_home_override(second_home)
        try:
            second = PlannerService(settings=self.service.settings, now_fn=lambda: NOW)
            self.assertEqual(second.list(), [])
        finally:
            reset_hermes_home_override(second_token)

    def test_cron_setup_uses_public_api_and_is_idempotent(self):
        class FakeCron:
            def __init__(self):
                self.jobs = []

            def list_jobs(self, include_disabled=False):
                return list(self.jobs)

            def create_job(self, **values):
                job = {"id": "cron-1", **values, "enabled": True, "state": "scheduled"}
                self.jobs.append(job)
                return job

            def update_job(self, job_id, updates):
                self.jobs[0].update(updates)
                return dict(self.jobs[0])

        fake = FakeCron()
        plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "personal_planner"
        first = ensure_dispatch_job(cron_api=fake, plugin_dir=plugin_dir)
        second = ensure_dispatch_job(cron_api=fake, plugin_dir=plugin_dir)

        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "updated")
        self.assertEqual(len(fake.jobs), 1)
        self.assertEqual(fake.jobs[0]["name"], DISPATCH_JOB_NAME)
        script = self.home / "scripts" / "personal_planner_dispatch.py"
        self.assertTrue(script.exists())
        compile(script.read_text(encoding="utf-8"), str(script), "exec")

    def test_review_cron_setup_is_explicit_and_idempotent(self):
        class FakeCron:
            def __init__(self):
                self.jobs = []

            def list_jobs(self, include_disabled=False):
                return list(self.jobs)

            def create_job(self, **values):
                job = {
                    "id": "review-cron-1",
                    **values,
                    "enabled": True,
                    "state": "scheduled",
                }
                self.jobs.append(job)
                return job

            def update_job(self, job_id, updates):
                self.jobs[0].update(updates)
                return dict(self.jobs[0])

        fake = FakeCron()
        plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "personal_planner"
        first = ensure_review_job(cron_api=fake, plugin_dir=plugin_dir)
        second = ensure_review_job(cron_api=fake, plugin_dir=plugin_dir)

        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "updated")
        self.assertEqual(len(fake.jobs), 1)
        self.assertEqual(fake.jobs[0]["name"], REVIEW_JOB_NAME)
        self.assertEqual(fake.jobs[0]["schedule"], "30 21 * * *")
        script = self.home / "scripts" / "personal_planner_daily_review.py"
        self.assertTrue(script.exists())
        source = script.read_text(encoding="utf-8")
        self.assertIn("render_daily_review_message", source)
        compile(source, str(script), "exec")

    def test_ingest_cron_setup_is_explicit_and_idempotent(self):
        class FakeCron:
            def __init__(self):
                self.jobs = []

            def list_jobs(self, include_disabled=False):
                return list(self.jobs)

            def create_job(self, **values):
                job = {
                    "id": "ingest-cron-1",
                    **values,
                    "enabled": True,
                    "state": "scheduled",
                }
                self.jobs.append(job)
                return job

            def update_job(self, job_id, updates):
                self.jobs[0].update(updates)
                return dict(self.jobs[0])

        fake = FakeCron()
        plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "personal_planner"
        first = ensure_ingest_job(cron_api=fake, plugin_dir=plugin_dir)
        second = ensure_ingest_job(cron_api=fake, plugin_dir=plugin_dir)

        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "updated")
        self.assertEqual(len(fake.jobs), 1)
        self.assertEqual(fake.jobs[0]["name"], INGEST_JOB_NAME)
        self.assertEqual(fake.jobs[0]["schedule"], "every 30m")
        self.assertTrue(fake.jobs[0]["no_agent"])
        script = self.home / "scripts" / "personal_planner_inbox_scan.py"
        self.assertTrue(script.exists())
        source = script.read_text(encoding="utf-8")
        self.assertIn("scan_ingest_sources", source)
        self.assertIn("render_ingest_message", source)
        compile(source, str(script), "exec")

    def test_radar_cron_is_agent_backed_and_idempotent(self):
        class FakeCron:
            def __init__(self):
                self.jobs = []

            def list_jobs(self, include_disabled=False):
                return list(self.jobs)

            def create_job(self, **values):
                job = {
                    "id": "radar-cron-1",
                    **values,
                    "enabled": True,
                    "state": "scheduled",
                    "schedule_display": values["schedule"],
                }
                self.jobs.append(job)
                return job

            def update_job(self, job_id, updates):
                self.jobs[0].update(updates)
                return dict(self.jobs[0])

        fake = FakeCron()
        first = ensure_radar_job(cron_api=fake)
        second = ensure_radar_job(cron_api=fake)

        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "updated")
        self.assertEqual(len(fake.jobs), 1)
        self.assertEqual(fake.jobs[0]["name"], RADAR_JOB_NAME)
        self.assertFalse(fake.jobs[0].get("no_agent", False))
        self.assertIn("monitor_status=approved", RADAR_PROMPT)
        self.assertIn("source_uri", fake.jobs[0]["prompt"])

    def test_planning_cron_jobs_are_agent_backed_and_draft_only(self):
        class FakeCron:
            def __init__(self):
                self.jobs = []

            def list_jobs(self, include_disabled=False):
                return list(self.jobs)

            def create_job(self, **values):
                job = {
                    "id": f"agent-cron-{len(self.jobs) + 1}",
                    **values,
                    "enabled": True,
                    "state": "scheduled",
                }
                self.jobs.append(job)
                return job

            def update_job(self, job_id, updates):
                job = next(value for value in self.jobs if value["id"] == job_id)
                job.update(updates)
                return dict(job)

        fake = FakeCron()
        daily = ensure_agent_daily_job(cron_api=fake)
        weekly = ensure_agent_weekly_job(cron_api=fake)
        updated = ensure_agent_daily_job(cron_api=fake)

        self.assertEqual(daily["action"], "created")
        self.assertEqual(weekly["action"], "created")
        self.assertEqual(updated["action"], "updated")
        self.assertEqual(len(fake.jobs), 2)
        self.assertEqual(fake.jobs[0]["name"], AGENT_DAILY_JOB_NAME)
        self.assertEqual(fake.jobs[1]["name"], AGENT_WEEKLY_JOB_NAME)
        self.assertFalse(fake.jobs[0].get("no_agent", False))
        self.assertFalse(fake.jobs[1].get("no_agent", False))
        self.assertIn("Never confirm", AGENT_DAILY_PROMPT)
        self.assertIn("Never confirm", AGENT_WEEKLY_PROMPT)
        self.assertIn("daily.review_due", AGENT_DAILY_PROMPT)
        self.assertIn("at most 20 pending events", AGENT_WEEKLY_PROMPT)
        self.assertIn("expert_analyze", AGENT_WEEKLY_PROMPT)
        self.assertIn("expert_prepare", AGENT_WEEKLY_PROMPT)
        self.assertIn("[SILENT]", AGENT_WEEKLY_PROMPT)

    def test_milestone_cadence_settings_control_existing_cron_jobs(self):
        class FakeCron:
            def __init__(self):
                self.jobs = []

            def list_jobs(self, include_disabled=False):
                return list(self.jobs)

            def create_job(self, **values):
                job = {
                    "id": f"cron-{len(self.jobs) + 1}",
                    **values,
                    "enabled": True,
                    "state": "scheduled",
                }
                self.jobs.append(job)
                return job

            def update_job(self, job_id, updates):
                job = next(value for value in self.jobs if value["id"] == job_id)
                job.update(updates)
                return dict(job)

        fake = FakeCron()
        settings = PlannerSettings(
            morning_preview_schedule="15 7 * * *",
            end_of_day_checkin_schedule="45 22 * * *",
        )
        plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "personal_planner"
        with patch(
            "plugins.personal_planner.cron_support.load_settings",
            return_value=settings,
        ):
            ensure_review_job(cron_api=fake, plugin_dir=plugin_dir)
            ensure_agent_daily_job(cron_api=fake)

        schedules = {job["name"]: job["schedule"] for job in fake.jobs}
        self.assertEqual(schedules[REVIEW_JOB_NAME], "45 22 * * *")
        self.assertEqual(schedules[AGENT_DAILY_JOB_NAME], "15 7 * * *")

    def test_milestone_cadence_settings_fall_back_to_legacy_names(self):
        with patch(
            "plugins.personal_planner.config._entry_config",
            return_value={
                "agent_daily_schedule": "5 6 * * *",
                "review_schedule": "10 23 * * *",
            },
        ):
            settings = load_settings()

        self.assertEqual(settings.morning_preview_schedule, "5 6 * * *")
        self.assertEqual(settings.end_of_day_checkin_schedule, "10 23 * * *")

    def test_complete_automation_setup_and_pause_are_reversible(self):
        class FakeCron:
            def __init__(self):
                self.jobs = []

            def list_jobs(self, include_disabled=False):
                return list(self.jobs)

            def create_job(self, **values):
                job = {
                    "id": f"cron-{len(self.jobs) + 1}",
                    **values,
                    "enabled": True,
                    "state": "scheduled",
                    "schedule_display": values["schedule"],
                }
                self.jobs.append(job)
                return job

            def update_job(self, job_id, updates):
                job = next(value for value in self.jobs if value["id"] == job_id)
                job.update(updates)
                return dict(job)

            def pause_job(self, job_id, reason=None):
                return self.update_job(
                    job_id,
                    {
                        "enabled": False,
                        "state": "paused",
                        "paused_reason": reason,
                    },
                )

        fake = FakeCron()
        enabled = ensure_automation_jobs(cron_api=fake)
        paused = pause_automation_jobs(cron_api=fake)
        status = automation_status(cron_api=fake)

        self.assertEqual(enabled["enabled_count"], 6)
        self.assertEqual(
            set(enabled["jobs"]),
            {
                "dispatch",
                "review",
                "ingest",
                "radar",
                "agent_daily",
                "agent_weekly",
            },
        )
        self.assertEqual(len(paused["paused_job_ids"]), 6)
        self.assertFalse(status["enabled"])
        self.assertTrue(all(job["state"] == "paused" for job in status["jobs"].values()))

    def test_plugin_registers_only_its_own_surfaces(self):
        from plugins import personal_planner

        ctx = SimpleNamespace(
            llm=object(), cli=[], commands=[], tools=[], hooks=[],
            register_cli_command=lambda **kw: ctx.cli.append(kw),
            register_command=lambda **kw: ctx.commands.append(kw),
            register_tool=lambda **kw: ctx.tools.append(kw),
            register_hook=lambda *args: ctx.hooks.append(args),
        )
        personal_planner.register(ctx)
        self.assertEqual([x["name"] for x in ctx.cli], ["plan"])
        self.assertEqual([x["name"] for x in ctx.commands], ["plan"])
        self.assertEqual([x["name"] for x in ctx.tools], ["personal_planner"])
        self.assertEqual([x[0] for x in ctx.hooks], ["pre_llm_call"])


if __name__ == "__main__":
    unittest.main()
