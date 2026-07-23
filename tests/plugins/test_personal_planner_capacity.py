from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from plugins.personal_planner.capacity import build_daily_capacity
from plugins.personal_planner.config import PlannerSettings, load_settings


class DailyCapacityTest(unittest.TestCase):
    def test_fixed_commitment_recovery_and_buffer_reduce_capacity_without_mutation(self):
        availability = [
            {
                "weekday": 0,
                "start_minute": 540,
                "end_minute": 1260,
                "energy": "high",
                "enabled": 1,
            },
            {
                "weekday": 1,
                "start_minute": 540,
                "end_minute": 1260,
                "energy": "high",
                "enabled": 1,
            },
        ]
        commitments = [
            {
                "id": "classes",
                "starts_at": "2026-07-20T01:00:00Z",
                "ends_at": "2026-07-20T10:00:00Z",
                "title": "Classes",
            }
        ]
        original = copy.deepcopy(commitments)

        result = build_daily_capacity(
            availability=availability,
            fixed_commitments=commitments,
            window_start=datetime(2026, 7, 20, tzinfo=timezone.utc),
            window_end=datetime(2026, 7, 22, tzinfo=timezone.utc),
            timezone_name="Asia/Shanghai",
            buffer_percent=25,
            recovery_minutes=30,
        )

        monday = next(day for day in result if day["date"] == "2026-07-20")
        tuesday = next(day for day in result if day["date"] == "2026-07-21")
        self.assertEqual(monday["raw_minutes"], 150)
        self.assertEqual(monday["capacity_minutes"], 110)
        self.assertEqual(tuesday["capacity_minutes"], 540)
        self.assertEqual(monday["reminder_anchor"]["kind"], "after_commitment")
        self.assertEqual(monday["reminder_anchor"]["commitment_id"], "classes")
        self.assertEqual(
            monday["reminder_anchor"]["recommended_at"], "2026-07-20T10:30:00Z"
        )
        self.assertIn("Classes", monday["reasons"])
        self.assertEqual(commitments, original)

    def test_capacity_clamps_windows_and_merges_overlapping_commitments(self):
        result = build_daily_capacity(
            availability=[
                {
                    "weekday": 0,
                    "start_minute": 540,
                    "end_minute": 1020,
                    "energy": "medium",
                    "enabled": 1,
                }
            ],
            fixed_commitments=[
                {
                    "id": "meeting-1",
                    "starts_at": "2026-07-20T02:00:00Z",
                    "ends_at": "2026-07-20T04:00:00Z",
                    "title": "Meeting 1",
                },
                {
                    "id": "meeting-2",
                    "starts_at": "2026-07-20T03:00:00Z",
                    "ends_at": "2026-07-20T05:00:00Z",
                    "title": "Meeting 2",
                },
            ],
            window_start=datetime(2026, 7, 20, 4, tzinfo=timezone.utc),
            window_end=datetime(2026, 7, 20, 7, tzinfo=timezone.utc),
            timezone_name="Asia/Shanghai",
            buffer_percent=0,
            recovery_minutes=0,
        )

        self.assertEqual(result[0]["date"], "2026-07-20")
        self.assertEqual(result[0]["raw_minutes"], 120)
        self.assertEqual(result[0]["capacity_minutes"], 120)
        self.assertEqual(result[0]["capacity_class"], "medium")

    def test_empty_availability_is_deterministic_and_low_confidence(self):
        kwargs = {
            "availability": [],
            "fixed_commitments": [],
            "window_start": datetime(2026, 7, 20, tzinfo=timezone.utc),
            "window_end": datetime(2026, 7, 21, tzinfo=timezone.utc),
            "timezone_name": "Asia/Shanghai",
            "buffer_percent": 25,
            "recovery_minutes": 30,
        }

        first = build_daily_capacity(**kwargs)
        second = build_daily_capacity(**kwargs)

        self.assertEqual(first, second)
        self.assertTrue(all(day["capacity_minutes"] == 0 for day in first))
        self.assertTrue(all(day["confidence"] == 0.35 for day in first))

    def test_milestone_settings_have_safe_defaults_and_bounded_overrides(self):
        defaults = PlannerSettings()
        self.assertEqual(defaults.planning_granularity, "daily_milestone")
        self.assertEqual(defaults.reminder_mode, "recommended_window")
        self.assertEqual(defaults.daily_buffer_percent, 25)
        self.assertEqual(defaults.max_daily_domains, 2)
        self.assertTrue(defaults.protect_hard_deadline_day)

        with patch(
            "plugins.personal_planner.config._entry_config",
            return_value={
                "planning_granularity": "minute_blocks",
                "reminder_mode": "calendar_reservation",
                "daily_buffer_percent": 99,
                "max_daily_domains": 0,
                "protect_hard_deadline_day": "false",
                "reminder_recovery_minutes": -5,
                "reminder_merge_minutes": 999,
                "estimate_learning_enabled": "off",
                "morning_preview_schedule": "15 7 * * *",
                "end_of_day_checkin_schedule": "0 22 * * *",
            },
        ):
            settings = load_settings()

        self.assertEqual(settings.planning_granularity, "daily_milestone")
        self.assertEqual(settings.reminder_mode, "recommended_window")
        self.assertEqual(settings.daily_buffer_percent, 80)
        self.assertEqual(settings.max_daily_domains, 1)
        self.assertFalse(settings.protect_hard_deadline_day)
        self.assertEqual(settings.reminder_recovery_minutes, 0)
        self.assertEqual(settings.reminder_merge_minutes, 240)
        self.assertFalse(settings.estimate_learning_enabled)
        self.assertEqual(settings.morning_preview_schedule, "15 7 * * *")
        self.assertEqual(settings.end_of_day_checkin_schedule, "0 22 * * *")


if __name__ == "__main__":
    unittest.main()
