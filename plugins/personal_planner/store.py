"""SQLite persistence for Personal Planner.

The schema is deliberately separate from SessionDB, Cron, Todo, Goal, and
Kanban.  It is profile-scoped through ``get_hermes_home()`` and can be removed
without changing any Hermes-owned database.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from hermes_constants import get_hermes_home

from .contracts import VALID_DOMAINS, validate_work_package
from .fingerprint import content_hash, source_key


SCHEMA_VERSION = 11


def planner_db_path() -> Path:
    return Path(get_hermes_home()) / "planner" / "planner.db"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


class PlannerStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else planner_db_path()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema(conn)
        return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS planner_items (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                raw_content     TEXT NOT NULL,
                source_type     TEXT NOT NULL DEFAULT 'manual',
                source_uri      TEXT,
                source_key      TEXT,
                content_hash    TEXT,
                discovered_at   TEXT NOT NULL,
                actionable_at   TEXT,
                due_at          TEXT,
                event_at        TEXT,
                timezone        TEXT NOT NULL,
                importance      TEXT NOT NULL DEFAULT 'normal'
                                CHECK (importance IN ('low', 'normal', 'high', 'critical')),
                status          TEXT NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft', 'confirmed', 'done', 'cancelled')),
                acknowledged_at TEXT,
                completed_at    TEXT,
                snoozed_until   TEXT,
                event_duration_minutes INTEGER NOT NULL DEFAULT 60,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_nudges (
                id              TEXT PRIMARY KEY,
                item_id         TEXT NOT NULL REFERENCES planner_items(id) ON DELETE CASCADE,
                kind            TEXT NOT NULL,
                fire_at         TEXT NOT NULL,
                severity        TEXT NOT NULL,
                ack_required    INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'sent', 'acknowledged', 'cancelled')),
                sent_at         TEXT,
                acknowledged_at TEXT,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_item_revisions (
                id              TEXT PRIMARY KEY,
                item_id         TEXT NOT NULL REFERENCES planner_items(id) ON DELETE CASCADE,
                previous_json   TEXT NOT NULL,
                changed_fields  TEXT NOT NULL,
                observed_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_calendar_links (
                id                  TEXT PRIMARY KEY,
                item_id             TEXT NOT NULL REFERENCES planner_items(id) ON DELETE CASCADE,
                provider            TEXT NOT NULL,
                calendar_id         TEXT NOT NULL,
                event_kind          TEXT NOT NULL,
                external_event_id   TEXT,
                html_link           TEXT,
                payload_hash        TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'creating'
                                    CHECK (status IN ('creating', 'active', 'stale', 'failed')),
                last_error          TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE (item_id, provider, calendar_id, event_kind)
            );

            CREATE TABLE IF NOT EXISTS planner_goals (
                id                      TEXT PRIMARY KEY,
                title                   TEXT NOT NULL,
                area                    TEXT NOT NULL DEFAULT 'personal',
                outcome                 TEXT,
                target_date             TEXT,
                weekly_target_minutes   INTEGER NOT NULL DEFAULT 0,
                priority                TEXT NOT NULL DEFAULT 'normal'
                                        CHECK (priority IN ('low', 'normal', 'high', 'critical')),
                status                  TEXT NOT NULL DEFAULT 'active'
                                        CHECK (status IN ('active', 'paused', 'done', 'cancelled')),
                created_at              TEXT NOT NULL,
                updated_at              TEXT NOT NULL,
                completed_at            TEXT
            );

            CREATE TABLE IF NOT EXISTS planner_tasks (
                id                  TEXT PRIMARY KEY,
                goal_id             TEXT REFERENCES planner_goals(id) ON DELETE SET NULL,
                planner_item_id     TEXT REFERENCES planner_items(id) ON DELETE SET NULL,
                title               TEXT NOT NULL,
                notes               TEXT,
                earliest_start      TEXT,
                due_at              TEXT,
                estimated_minutes   INTEGER NOT NULL,
                completed_minutes   INTEGER NOT NULL DEFAULT 0,
                min_block_minutes   INTEGER NOT NULL DEFAULT 30,
                splittable          INTEGER NOT NULL DEFAULT 1,
                energy              TEXT NOT NULL DEFAULT 'medium'
                                    CHECK (energy IN ('low', 'medium', 'high')),
                priority            TEXT NOT NULL DEFAULT 'normal'
                                    CHECK (priority IN ('low', 'normal', 'high', 'critical')),
                status              TEXT NOT NULL DEFAULT 'backlog'
                                    CHECK (status IN ('backlog', 'planned', 'done', 'cancelled')),
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                completed_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS planner_availability (
                id              TEXT PRIMARY KEY,
                weekday         INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                start_minute    INTEGER NOT NULL CHECK (start_minute BETWEEN 0 AND 1439),
                end_minute      INTEGER NOT NULL CHECK (end_minute BETWEEN 1 AND 1440),
                energy          TEXT NOT NULL DEFAULT 'medium'
                                CHECK (energy IN ('low', 'medium', 'high')),
                label           TEXT,
                enabled         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                CHECK (start_minute < end_minute),
                UNIQUE (weekday, start_minute, end_minute)
            );

            CREATE TABLE IF NOT EXISTS planner_plan_runs (
                id                  TEXT PRIMARY KEY,
                window_start        TEXT NOT NULL,
                window_end          TEXT NOT NULL,
                timezone            TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'draft'
                                    CHECK (status IN ('draft', 'confirmed', 'superseded', 'cancelled')),
                is_replan           INTEGER NOT NULL DEFAULT 0,
                replaces_plan_ids   TEXT NOT NULL DEFAULT '[]',
                summary_json        TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                confirmed_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS planner_plan_blocks (
                id                  TEXT PRIMARY KEY,
                plan_id             TEXT NOT NULL REFERENCES planner_plan_runs(id) ON DELETE CASCADE,
                task_id             TEXT NOT NULL REFERENCES planner_tasks(id) ON DELETE CASCADE,
                starts_at           TEXT NOT NULL,
                ends_at             TEXT NOT NULL,
                minutes             INTEGER NOT NULL,
                energy              TEXT NOT NULL,
                availability_label  TEXT,
                priority_score      INTEGER NOT NULL DEFAULT 0,
                status              TEXT NOT NULL DEFAULT 'proposed'
                                    CHECK (status IN ('proposed', 'confirmed', 'superseded', 'done', 'skipped')),
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE (plan_id, task_id, starts_at)
            );

            CREATE TABLE IF NOT EXISTS planner_task_progress_log (
                id              TEXT PRIMARY KEY,
                task_id         TEXT NOT NULL REFERENCES planner_tasks(id) ON DELETE CASCADE,
                minutes         INTEGER NOT NULL CHECK (minutes > 0),
                source          TEXT NOT NULL DEFAULT 'manual',
                note            TEXT,
                recorded_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_calendar_busy_cache (
                id              TEXT PRIMARY KEY,
                provider        TEXT NOT NULL,
                calendar_id     TEXT NOT NULL,
                event_id        TEXT NOT NULL,
                summary         TEXT NOT NULL,
                starts_at       TEXT NOT NULL,
                ends_at         TEXT NOT NULL,
                all_day         INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'confirmed',
                html_link       TEXT,
                raw_json        TEXT NOT NULL,
                fetched_at      TEXT NOT NULL,
                expires_at      TEXT NOT NULL,
                UNIQUE (provider, calendar_id, event_id)
            );

            CREATE TABLE IF NOT EXISTS planner_calendar_sync_state (
                id              TEXT PRIMARY KEY,
                provider        TEXT NOT NULL,
                calendar_id     TEXT NOT NULL,
                window_start    TEXT NOT NULL,
                window_end      TEXT NOT NULL,
                event_count     INTEGER NOT NULL DEFAULT 0,
                snapshot_hash   TEXT NOT NULL,
                fetched_at      TEXT NOT NULL,
                expires_at      TEXT NOT NULL,
                UNIQUE (provider, calendar_id, window_start, window_end)
            );

            CREATE TABLE IF NOT EXISTS planner_block_checkins (
                id                  TEXT PRIMARY KEY,
                block_id            TEXT NOT NULL UNIQUE
                                    REFERENCES planner_plan_blocks(id) ON DELETE CASCADE,
                task_id             TEXT NOT NULL
                                    REFERENCES planner_tasks(id) ON DELETE CASCADE,
                outcome             TEXT NOT NULL
                                    CHECK (outcome IN ('done', 'skipped', 'deferred')),
                actual_minutes      INTEGER
                                    CHECK (
                                        (outcome = 'done' AND actual_minutes > 0)
                                        OR (
                                            outcome IN ('skipped', 'deferred')
                                            AND actual_minutes IS NULL
                                        )
                                    ),
                credited_minutes    INTEGER NOT NULL DEFAULT 0
                                    CHECK (credited_minutes >= 0),
                note                TEXT,
                recorded_at         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_plan_block_calendar_links (
                id                  TEXT PRIMARY KEY,
                block_id            TEXT NOT NULL
                                    REFERENCES planner_plan_blocks(id) ON DELETE CASCADE,
                provider            TEXT NOT NULL,
                calendar_id         TEXT NOT NULL,
                external_event_id   TEXT,
                html_link           TEXT,
                payload_hash        TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'creating'
                                    CHECK (status IN ('creating', 'active', 'stale', 'failed')),
                last_error          TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE (block_id, provider, calendar_id)
            );

            CREATE TABLE IF NOT EXISTS planner_plan_calendar_previews (
                plan_id             TEXT NOT NULL
                                    REFERENCES planner_plan_runs(id) ON DELETE CASCADE,
                provider            TEXT NOT NULL,
                calendar_id         TEXT NOT NULL,
                snapshot_hash       TEXT NOT NULL,
                event_count         INTEGER NOT NULL DEFAULT 0
                                    CHECK (event_count >= 0),
                previewed_at        TEXT NOT NULL,
                PRIMARY KEY (plan_id, provider, calendar_id)
            );

            CREATE TABLE IF NOT EXISTS planner_ingest_sources (
                id                  TEXT PRIMARY KEY,
                provider            TEXT NOT NULL
                                    CHECK (provider IN ('gmail')),
                name                TEXT NOT NULL,
                query               TEXT NOT NULL,
                importance          TEXT NOT NULL DEFAULT 'normal'
                                    CHECK (importance IN ('low', 'normal', 'high', 'critical')),
                max_results         INTEGER NOT NULL DEFAULT 25
                                    CHECK (max_results BETWEEN 1 AND 100),
                enabled             INTEGER NOT NULL DEFAULT 1,
                last_scan_at        TEXT,
                last_success_at     TEXT,
                last_error          TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE (provider, name)
            );

            CREATE TABLE IF NOT EXISTS planner_ingest_receipts (
                id                  TEXT PRIMARY KEY,
                source_id           TEXT NOT NULL
                                    REFERENCES planner_ingest_sources(id) ON DELETE CASCADE,
                provider_item_id    TEXT NOT NULL,
                provider_thread_id  TEXT,
                item_id             TEXT
                                    REFERENCES planner_items(id) ON DELETE SET NULL,
                capture_outcome     TEXT NOT NULL
                                    CHECK (capture_outcome IN ('created', 'duplicate', 'updated')),
                subject             TEXT,
                sender              TEXT,
                received_at         TEXT,
                scanned_at          TEXT NOT NULL,
                UNIQUE (source_id, provider_item_id)
            );

            CREATE TABLE IF NOT EXISTS planner_profile_facts (
                id                  TEXT PRIMARY KEY,
                category            TEXT NOT NULL,
                fact_key            TEXT NOT NULL,
                fact_value          TEXT NOT NULL,
                normalized_value    TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'proposed'
                                    CHECK (status IN (
                                        'proposed', 'confirmed', 'rejected',
                                        'revoked', 'superseded'
                                    )),
                privacy             TEXT NOT NULL DEFAULT 'standard'
                                    CHECK (privacy IN ('standard', 'sensitive')),
                source_type         TEXT NOT NULL DEFAULT 'user',
                source_uri          TEXT,
                confidence          REAL NOT NULL DEFAULT 1.0
                                    CHECK (confidence BETWEEN 0.0 AND 1.0),
                valid_until         TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                confirmed_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS planner_monitor_proposals (
                id                  TEXT PRIMARY KEY,
                proposal_key        TEXT NOT NULL UNIQUE,
                topic_key           TEXT NOT NULL,
                category            TEXT NOT NULL,
                title               TEXT NOT NULL,
                rationale           TEXT NOT NULL,
                priority            TEXT NOT NULL DEFAULT 'normal'
                                    CHECK (priority IN ('low', 'normal', 'high', 'critical')),
                cadence             TEXT NOT NULL,
                source_kinds_json   TEXT NOT NULL,
                query_terms_json    TEXT NOT NULL,
                evidence_json       TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'proposed'
                                    CHECK (status IN (
                                        'proposed', 'approved', 'rejected',
                                        'superseded'
                                    )),
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                decided_at          TEXT
            );

            CREATE TABLE IF NOT EXISTS planner_events (
                id                  TEXT PRIMARY KEY,
                event_type          TEXT NOT NULL,
                source              TEXT NOT NULL,
                dedupe_key          TEXT NOT NULL UNIQUE,
                payload_json        TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'processed', 'failed')),
                occurred_at         TEXT NOT NULL,
                processed_at        TEXT,
                created_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_expert_results (
                id                  TEXT PRIMARY KEY,
                event_id            TEXT NOT NULL REFERENCES planner_events(id) ON DELETE CASCADE,
                domain              TEXT NOT NULL
                                    CHECK (domain IN ('research', 'career', 'communication')),
                result_json         TEXT NOT NULL,
                provider            TEXT,
                model               TEXT,
                input_tokens        INTEGER NOT NULL DEFAULT 0,
                output_tokens       INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_work_packages (
                id                  TEXT PRIMARY KEY,
                domain              TEXT NOT NULL
                                    CHECK (domain IN ('research', 'career', 'communication')),
                objective           TEXT NOT NULL,
                hard_deadline       TEXT NOT NULL,
                internal_deadline   TEXT,
                p50_minutes         INTEGER NOT NULL CHECK (p50_minutes > 0),
                p90_minutes         INTEGER NOT NULL CHECK (p90_minutes > 0),
                confidence          REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
                status              TEXT NOT NULL DEFAULT 'proposed'
                                    CHECK (status IN (
                                        'proposed', 'active', 'done', 'cancelled',
                                        'superseded'
                                    )),
                package_json        TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_milestone_plan_runs (
                id                  TEXT PRIMARY KEY,
                window_start        TEXT NOT NULL,
                window_end          TEXT NOT NULL,
                timezone            TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'draft'
                                    CHECK (status IN (
                                        'draft', 'confirmed', 'rejected',
                                        'superseded', 'cancelled'
                                    )),
                summary_json        TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                confirmed_at        TEXT,
                rejected_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS planner_daily_milestones (
                id                  TEXT PRIMARY KEY,
                plan_id             TEXT NOT NULL REFERENCES planner_milestone_plan_runs(id) ON DELETE CASCADE,
                allocation_key      TEXT NOT NULL,
                milestone_date      TEXT NOT NULL,
                window_end          TEXT,
                domain              TEXT NOT NULL
                                    CHECK (domain IN ('research', 'career', 'communication')),
                work_package_id     TEXT REFERENCES planner_work_packages(id) ON DELETE SET NULL,
                title               TEXT NOT NULL,
                target_minutes      INTEGER NOT NULL CHECK (target_minutes > 0),
                definition_json     TEXT NOT NULL,
                source_step_ids_json TEXT NOT NULL,
                reminder_anchor_json TEXT NOT NULL,
                explanation_json    TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'draft'
                                    CHECK (status IN (
                                        'draft', 'planned', 'in_progress', 'done',
                                        'partial', 'missed', 'blocked', 'superseded',
                                        'cancelled'
                                    )),
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE (plan_id, allocation_key)
            );

            CREATE TABLE IF NOT EXISTS planner_milestone_checkins (
                id                  TEXT PRIMARY KEY,
                milestone_id        TEXT NOT NULL UNIQUE REFERENCES planner_daily_milestones(id) ON DELETE CASCADE,
                outcome             TEXT NOT NULL
                                    CHECK (outcome IN (
                                        'done', 'partial', 'not_started', 'blocked',
                                        'skipped'
                                    )),
                actual_minutes      INTEGER NOT NULL DEFAULT 0 CHECK (actual_minutes >= 0),
                completion_fraction REAL NOT NULL DEFAULT 0.0
                                    CHECK (completion_fraction BETWEEN 0.0 AND 1.0),
                note                TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_estimate_observations (
                id                  TEXT PRIMARY KEY,
                checkin_id          TEXT NOT NULL REFERENCES planner_milestone_checkins(id) ON DELETE CASCADE,
                milestone_id        TEXT NOT NULL REFERENCES planner_daily_milestones(id) ON DELETE CASCADE,
                work_package_id     TEXT NOT NULL REFERENCES planner_work_packages(id) ON DELETE CASCADE,
                source_step_id      TEXT NOT NULL,
                allocation_key      TEXT NOT NULL,
                estimated_p50_minutes INTEGER NOT NULL,
                estimated_p90_minutes INTEGER NOT NULL,
                credited_p50_minutes INTEGER NOT NULL,
                credited_p90_minutes INTEGER NOT NULL,
                remaining_adjustment_p50_minutes INTEGER NOT NULL DEFAULT 0,
                remaining_adjustment_p90_minutes INTEGER NOT NULL DEFAULT 0,
                actual_minutes      INTEGER,
                completion_fraction REAL,
                outcome             TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                UNIQUE (checkin_id, source_step_id)
            );

            CREATE TABLE IF NOT EXISTS planner_milestone_reminder_deliveries (
                id                  TEXT PRIMARY KEY,
                milestone_id        TEXT NOT NULL REFERENCES planner_daily_milestones(id) ON DELETE CASCADE,
                dedupe_key          TEXT NOT NULL UNIQUE,
                batch_id            TEXT NOT NULL,
                scheduled_for       TEXT NOT NULL,
                status              TEXT NOT NULL
                                    CHECK (status IN ('pending', 'retryable', 'sent')),
                emitted_at          TEXT NOT NULL,
                delivered_at        TEXT,
                delivery_target     TEXT NOT NULL,
                attempt_count       INTEGER NOT NULL DEFAULT 1,
                last_error          TEXT,
                created_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_agent_runs (
                id                  TEXT PRIMARY KEY,
                trigger_event_id    TEXT REFERENCES planner_events(id) ON DELETE SET NULL,
                selected_experts_json TEXT NOT NULL,
                expert_result_ids_json TEXT NOT NULL,
                decision_json       TEXT NOT NULL,
                draft_plan_id       TEXT REFERENCES planner_milestone_plan_runs(id) ON DELETE SET NULL,
                approval_status     TEXT NOT NULL DEFAULT 'not_requested'
                                    CHECK (approval_status IN (
                                        'not_requested', 'pending', 'approved', 'rejected'
                                    )),
                outcome             TEXT NOT NULL,
                reasoning_summary   TEXT NOT NULL,
                usage_json          TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_planner_items_status_due
                ON planner_items(status, due_at);
            CREATE INDEX IF NOT EXISTS idx_planner_nudges_due
                ON planner_nudges(status, fire_at);
            CREATE INDEX IF NOT EXISTS idx_planner_revisions_item
                ON planner_item_revisions(item_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_planner_calendar_item
                ON planner_calendar_links(item_id, status);
            CREATE INDEX IF NOT EXISTS idx_planner_goals_status_target
                ON planner_goals(status, target_date);
            CREATE INDEX IF NOT EXISTS idx_planner_tasks_status_due
                ON planner_tasks(status, due_at);
            CREATE INDEX IF NOT EXISTS idx_planner_tasks_goal
                ON planner_tasks(goal_id, status);
            CREATE INDEX IF NOT EXISTS idx_planner_availability_weekday
                ON planner_availability(weekday, start_minute);
            CREATE INDEX IF NOT EXISTS idx_planner_plan_runs_window
                ON planner_plan_runs(status, window_start, window_end);
            CREATE INDEX IF NOT EXISTS idx_planner_plan_blocks_window
                ON planner_plan_blocks(status, starts_at, ends_at);
            CREATE INDEX IF NOT EXISTS idx_planner_progress_task_time
                ON planner_task_progress_log(task_id, recorded_at);
            CREATE INDEX IF NOT EXISTS idx_planner_busy_cache_window
                ON planner_calendar_busy_cache(provider, calendar_id, starts_at, ends_at);
            CREATE INDEX IF NOT EXISTS idx_planner_calendar_sync_state_window
                ON planner_calendar_sync_state(provider, calendar_id, window_start, window_end);
            CREATE INDEX IF NOT EXISTS idx_planner_block_checkins_time
                ON planner_block_checkins(recorded_at, outcome);
            CREATE INDEX IF NOT EXISTS idx_planner_block_calendar_status
                ON planner_plan_block_calendar_links(status, block_id);
            CREATE INDEX IF NOT EXISTS idx_planner_plan_calendar_preview_time
                ON planner_plan_calendar_previews(previewed_at);
            CREATE INDEX IF NOT EXISTS idx_planner_ingest_sources_enabled
                ON planner_ingest_sources(enabled, provider);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_planner_ingest_source_name
                ON planner_ingest_sources(provider, name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_planner_ingest_receipts_time
                ON planner_ingest_receipts(source_id, scanned_at);
            CREATE INDEX IF NOT EXISTS idx_planner_profile_fact_status
                ON planner_profile_facts(status, category, fact_key);
            CREATE INDEX IF NOT EXISTS idx_planner_monitor_proposal_status
                ON planner_monitor_proposals(status, category, topic_key);
            CREATE INDEX IF NOT EXISTS idx_planner_events_status_time
                ON planner_events(status, occurred_at);
            CREATE INDEX IF NOT EXISTS idx_planner_expert_results_event
                ON planner_expert_results(event_id, domain);
            CREATE INDEX IF NOT EXISTS idx_planner_work_packages_status_deadline
                ON planner_work_packages(status, hard_deadline);
            CREATE INDEX IF NOT EXISTS idx_planner_milestone_plans_window
                ON planner_milestone_plan_runs(status, window_start, window_end);
            CREATE INDEX IF NOT EXISTS idx_planner_daily_milestones_date
                ON planner_daily_milestones(status, milestone_date);
            CREATE INDEX IF NOT EXISTS idx_planner_daily_milestones_package
                ON planner_daily_milestones(work_package_id, milestone_date);
            CREATE INDEX IF NOT EXISTS idx_planner_estimate_package
                ON planner_estimate_observations(work_package_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_planner_milestone_reminder_status
                ON planner_milestone_reminder_deliveries(status, emitted_at);
            CREATE INDEX IF NOT EXISTS idx_planner_milestone_reminder_batch
                ON planner_milestone_reminder_deliveries(batch_id, status);
            CREATE INDEX IF NOT EXISTS idx_planner_agent_runs_time
                ON planner_agent_runs(created_at, outcome);
            """
        )
        PlannerStore._ensure_column(conn, "planner_items", "source_key", "TEXT")
        PlannerStore._ensure_column(conn, "planner_items", "content_hash", "TEXT")
        PlannerStore._ensure_column(conn, "planner_agent_runs", "duration_ms", "INTEGER")
        PlannerStore._ensure_column(
            conn,
            "planner_items",
            "event_duration_minutes",
            "INTEGER NOT NULL DEFAULT 60",
        )
        PlannerStore._ensure_column(
            conn, "planner_calendar_links", "last_error", "TEXT"
        )
        PlannerStore._backfill_fingerprints(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_planner_items_source_key "
            "ON planner_items(source_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_planner_items_content_hash "
            "ON planner_items(content_hash)"
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, declaration: str
    ) -> None:
        names = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def _backfill_fingerprints(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT id, raw_content, source_type, source_uri, source_key, content_hash "
            "FROM planner_items WHERE source_key IS NULL OR content_hash IS NULL"
        ).fetchall()
        for row in rows:
            digest = content_hash(row["raw_content"])
            identity = source_key(
                source_type=row["source_type"],
                source_uri=row["source_uri"],
                raw_content=row["raw_content"],
            )
            conn.execute(
                "UPDATE planner_items SET source_key = ?, content_hash = ? WHERE id = ?",
                (identity, digest, row["id"]),
            )

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _decode_planner_event(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    @staticmethod
    def _decode_work_package(row: sqlite3.Row) -> dict[str, Any]:
        value = json.loads(row["package_json"])
        value.update(
            {
                "id": row["id"],
                "domain": row["domain"],
                "objective": row["objective"],
                "hard_deadline": row["hard_deadline"],
                "recommended_internal_deadline": row["internal_deadline"],
                "effort": {
                    **value["effort"],
                    "p50_minutes": row["p50_minutes"],
                    "p90_minutes": row["p90_minutes"],
                    "confidence": row["confidence"],
                },
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return value

    @staticmethod
    def _decode_daily_milestone(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["date"] = value.pop("milestone_date")
        value["definition_of_done"] = json.loads(value.pop("definition_json"))
        value["source_step_ids"] = json.loads(value.pop("source_step_ids_json"))
        value["reminder_anchor"] = json.loads(value.pop("reminder_anchor_json"))
        value["explanation"] = json.loads(value.pop("explanation_json"))
        return value

    @staticmethod
    def _decode_expert_result(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["result"] = json.loads(value.pop("result_json"))
        value["usage"] = {
            "input_tokens": value.pop("input_tokens"),
            "output_tokens": value.pop("output_tokens"),
        }
        return value

    @staticmethod
    def _decode_agent_run(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for target, source in (
            ("selected_experts", "selected_experts_json"),
            ("expert_result_ids", "expert_result_ids_json"),
            ("decision", "decision_json"),
            ("usage", "usage_json"),
        ):
            value[target] = json.loads(value.pop(source))
        return value

    def create_planner_event(
        self,
        *,
        event_type: str,
        source: str,
        dedupe_key: str,
        payload: dict[str, Any],
        occurred_at: str,
    ) -> dict[str, Any]:
        """Persist an input event once without changing the first-seen payload."""

        event_id = _new_id("EV")
        with self.write() as conn:
            conn.execute(
                "INSERT INTO planner_events "
                "(id, event_type, source, dedupe_key, payload_json, status, "
                "occurred_at, processed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, ?) "
                "ON CONFLICT(dedupe_key) DO NOTHING",
                (
                    event_id,
                    str(event_type).strip(),
                    str(source).strip(),
                    str(dedupe_key).strip(),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    occurred_at,
                    occurred_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_events WHERE dedupe_key = ?",
                (str(dedupe_key).strip(),),
            ).fetchone()
        assert row is not None
        return self._decode_planner_event(row)

    def list_planner_events(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        query = "SELECT * FROM planner_events"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY occurred_at, id LIMIT ?"
        params.append(limit)
        with self.read() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._decode_planner_event(row) for row in rows]

    def get_planner_event(self, event_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_events WHERE id = ?", (event_id.upper(),)
            ).fetchone()
        return self._decode_planner_event(row) if row is not None else None

    def set_planner_event_status(
        self,
        event_id: str,
        *,
        status: str,
        processed_at: str | None,
    ) -> dict[str, Any] | None:
        normalized = str(status).strip().lower()
        if normalized not in {"pending", "processed", "failed"}:
            raise ValueError("planner event status is invalid")
        event_id = event_id.upper()
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_events SET status = ?, processed_at = ? WHERE id = ?",
                (normalized, processed_at, event_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._decode_planner_event(row) if row is not None else None

    def save_expert_analysis_batch(
        self,
        *,
        event_id: str,
        selected_experts: list[str],
        routing_rationale: dict[str, Any],
        analyses: list[dict[str, Any]],
        usage: dict[str, Any],
        duration_ms: int | None,
        now: str,
    ) -> dict[str, Any]:
        """Atomically commit one complete expert-analysis decision."""

        event_id = event_id.upper()
        selected = list(dict.fromkeys(str(value).lower() for value in selected_experts))
        if not selected or any(value not in VALID_DOMAINS for value in selected):
            raise ValueError("selected expert domains are invalid")
        normalized: list[dict[str, Any]] = []
        for analysis in analyses:
            domain = str(analysis.get("domain") or "").strip().lower()
            if domain not in selected:
                raise ValueError("analysis domain was not selected")
            package = validate_work_package(dict(analysis.get("package") or {}))
            if package["domain"] != domain:
                raise ValueError("analysis and work package domains differ")
            normalized.append({**analysis, "domain": domain, "package": package})
        if {value["domain"] for value in normalized} != set(selected):
            raise ValueError("every selected expert must produce one analysis")

        expert_rows: list[sqlite3.Row] = []
        package_rows: list[sqlite3.Row] = []
        expert_ids: list[str] = []
        with self.write() as conn:
            event_row = conn.execute(
                "SELECT * FROM planner_events WHERE id = ?", (event_id,)
            ).fetchone()
            if event_row is None:
                raise ValueError(f"planner event {event_id} does not exist")
            for analysis in normalized:
                result_id = _new_id("ER")
                expert_ids.append(result_id)
                analysis_usage = dict(analysis.get("usage") or {})
                conn.execute(
                    "INSERT INTO planner_expert_results "
                    "(id, event_id, domain, result_json, provider, model, "
                    "input_tokens, output_tokens, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result_id,
                        event_id,
                        analysis["domain"],
                        json.dumps(
                            analysis["package"], ensure_ascii=False, sort_keys=True
                        ),
                        analysis.get("provider"),
                        analysis.get("model"),
                        max(0, int(analysis_usage.get("input_tokens") or 0)),
                        max(0, int(analysis_usage.get("output_tokens") or 0)),
                        now,
                    ),
                )
                package = analysis["package"]
                package_id = str(package.get("id") or _new_id("WP")).upper()
                effort = package["effort"]
                conn.execute(
                    "INSERT INTO planner_work_packages "
                    "(id, domain, objective, hard_deadline, internal_deadline, "
                    "p50_minutes, p90_minutes, confidence, status, package_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                    "'proposed', ?, ?, ?)",
                    (
                        package_id,
                        package["domain"],
                        package["objective"],
                        package["hard_deadline"],
                        package["recommended_internal_deadline"],
                        effort["p50_minutes"],
                        effort["p90_minutes"],
                        effort["confidence"],
                        json.dumps(package, ensure_ascii=False, sort_keys=True),
                        now,
                        now,
                    ),
                )
                expert_rows.append(
                    conn.execute(
                        "SELECT * FROM planner_expert_results WHERE id = ?",
                        (result_id,),
                    ).fetchone()
                )
                package_rows.append(
                    conn.execute(
                        "SELECT * FROM planner_work_packages WHERE id = ?",
                        (package_id,),
                    ).fetchone()
                )
            conn.execute(
                "UPDATE planner_events SET status = 'processed', processed_at = ? "
                "WHERE id = ?",
                (now, event_id),
            )
            run_id = _new_id("AR")
            decision = {
                "kind": "work_packages_proposed",
                "routing": routing_rationale,
            }
            conn.execute(
                "INSERT INTO planner_agent_runs "
                "(id, trigger_event_id, selected_experts_json, "
                "expert_result_ids_json, decision_json, draft_plan_id, "
                "approval_status, outcome, reasoning_summary, usage_json, "
                "created_at, updated_at, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, NULL, 'pending', 'analyzed', ?, ?, ?, ?, ?)",
                (
                    run_id,
                    event_id,
                    json.dumps(selected, ensure_ascii=False, sort_keys=True),
                    json.dumps(expert_ids, ensure_ascii=False, sort_keys=True),
                    json.dumps(decision, ensure_ascii=False, sort_keys=True),
                    "Validated expert WorkPackages are awaiting plan approval",
                    json.dumps(usage or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    duration_ms,
                ),
            )
            event_row = conn.execute(
                "SELECT * FROM planner_events WHERE id = ?", (event_id,)
            ).fetchone()
            run_row = conn.execute(
                "SELECT * FROM planner_agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert event_row is not None and run_row is not None
        return {
            "event": self._decode_planner_event(event_row),
            "expert_results": [self._decode_expert_result(row) for row in expert_rows],
            "work_packages": [self._decode_work_package(row) for row in package_rows],
            "agent_run": self._decode_agent_run(run_row),
        }

    def save_expert_result(
        self,
        *,
        event_id: str,
        domain: str,
        result: dict[str, Any],
        provider: str | None,
        model: str | None,
        usage: dict[str, Any] | None,
        now: str,
    ) -> dict[str, Any]:
        domain = str(domain).strip().lower()
        if domain not in VALID_DOMAINS:
            raise ValueError("expert result domain is invalid")
        usage_value = dict(usage or {})
        input_tokens = max(0, int(usage_value.get("input_tokens") or 0))
        output_tokens = max(0, int(usage_value.get("output_tokens") or 0))
        result_id = _new_id("ER")
        with self.write() as conn:
            conn.execute(
                "INSERT INTO planner_expert_results "
                "(id, event_id, domain, result_json, provider, model, input_tokens, "
                "output_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    event_id.upper(),
                    domain,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_expert_results WHERE id = ?", (result_id,)
            ).fetchone()
        assert row is not None
        return self._decode_expert_result(row)

    def list_expert_results(
        self, *, event_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        query = "SELECT * FROM planner_expert_results"
        params: list[Any] = []
        if event_id is not None:
            query += " WHERE event_id = ?"
            params.append(event_id.upper())
        query += " ORDER BY created_at, id LIMIT ?"
        params.append(limit)
        with self.read() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._decode_expert_result(row) for row in rows]

    def save_work_package(
        self, package: dict[str, Any], *, now: str
    ) -> dict[str, Any]:
        """Validate and persist a domain expert's proposed work package."""

        value = validate_work_package(package)
        package_id = str(value.get("id") or _new_id("WP")).upper()
        effort = value["effort"]
        with self.write() as conn:
            conn.execute(
                "INSERT INTO planner_work_packages "
                "(id, domain, objective, hard_deadline, internal_deadline, "
                "p50_minutes, p90_minutes, confidence, status, package_json, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)",
                (
                    package_id,
                    value["domain"],
                    value["objective"],
                    value["hard_deadline"],
                    value["recommended_internal_deadline"],
                    effort["p50_minutes"],
                    effort["p90_minutes"],
                    effort["confidence"],
                    json.dumps(value, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_work_packages WHERE id = ?", (package_id,)
            ).fetchone()
        assert row is not None
        return self._decode_work_package(row)

    def get_work_package(self, package_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_work_packages WHERE id = ?",
                (package_id.upper(),),
            ).fetchone()
        return self._decode_work_package(row) if row is not None else None

    def list_work_packages(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        query = "SELECT * FROM planner_work_packages"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at, id LIMIT ?"
        params.append(limit)
        with self.read() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._decode_work_package(row) for row in rows]

    def create_milestone_plan_draft(
        self,
        *,
        window_start: str,
        window_end: str,
        timezone: str,
        summary: dict[str, Any],
        milestones: Iterable[dict[str, Any]],
        now: str,
    ) -> dict[str, Any]:
        """Persist an immutable milestone proposal pending explicit approval."""

        plan_id = _new_id("MP")
        summary_value = dict(summary)
        if summary_value.get("base_plan_id"):
            summary_value["base_plan_id"] = str(
                summary_value["base_plan_id"]
            ).upper()
        milestone_values = [dict(value) for value in milestones]
        with self.write() as conn:
            conn.execute(
                "INSERT INTO planner_milestone_plan_runs "
                "(id, window_start, window_end, timezone, status, summary_json, "
                "created_at, updated_at, confirmed_at, rejected_at) "
                "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, NULL, NULL)",
                (
                    plan_id,
                    window_start,
                    window_end,
                    timezone,
                    json.dumps(summary_value, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            for milestone in milestone_values:
                allocation_key = str(milestone.get("allocation_key") or "").strip()
                domain = str(milestone.get("domain") or "").strip().lower()
                title = str(milestone.get("title") or "").strip()
                try:
                    target_minutes = int(milestone.get("target_minutes"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("milestone target_minutes must be an integer") from exc
                if not allocation_key:
                    raise ValueError("milestone allocation_key is required")
                if domain not in VALID_DOMAINS:
                    raise ValueError("milestone domain is invalid")
                if not title:
                    raise ValueError("milestone title is required")
                if target_minutes <= 0:
                    raise ValueError("milestone target_minutes must be positive")
                definition = [
                    str(item).strip()
                    for item in milestone.get("definition_of_done") or []
                    if str(item).strip()
                ]
                source_step_ids = [
                    str(item).strip()
                    for item in milestone.get("source_step_ids") or []
                    if str(item).strip()
                ]
                if not definition or not source_step_ids:
                    raise ValueError(
                        "milestone definition_of_done and source_step_ids are required"
                    )
                conn.execute(
                    "INSERT INTO planner_daily_milestones "
                    "(id, plan_id, allocation_key, milestone_date, window_end, "
                    "domain, work_package_id, title, target_minutes, definition_json, "
                    "source_step_ids_json, reminder_anchor_json, explanation_json, "
                    "status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                    (
                        _new_id("DM"),
                        plan_id,
                        allocation_key,
                        str(milestone.get("date") or "").strip(),
                        milestone.get("window_end"),
                        domain,
                        (
                            str(milestone["work_package_id"]).upper()
                            if milestone.get("work_package_id")
                            else None
                        ),
                        title,
                        target_minutes,
                        json.dumps(definition, ensure_ascii=False, sort_keys=True),
                        json.dumps(source_step_ids, ensure_ascii=False, sort_keys=True),
                        json.dumps(
                            milestone.get("reminder_anchor") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        json.dumps(
                            milestone.get("explanation") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        now,
                        now,
                    ),
                )
        result = self.get_milestone_plan(plan_id)
        assert result is not None
        return result

    def get_milestone_plan(self, plan_id: str) -> dict[str, Any] | None:
        plan_id = plan_id.upper()
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_milestone_plan_runs WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                return None
            milestone_rows = conn.execute(
                "SELECT * FROM planner_daily_milestones WHERE plan_id = ? "
                "ORDER BY milestone_date, allocation_key, id",
                (plan_id,),
            ).fetchall()
        result = dict(row)
        result["summary"] = json.loads(result.pop("summary_json"))
        result["milestones"] = [
            self._decode_daily_milestone(value) for value in milestone_rows
        ]
        return result

    def list_milestone_plans(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        query = (
            "SELECT p.*, COUNT(m.id) AS milestone_count "
            "FROM planner_milestone_plan_runs p "
            "LEFT JOIN planner_daily_milestones m ON m.plan_id = p.id"
        )
        params: list[Any] = []
        if status is not None:
            query += " WHERE p.status = ?"
            params.append(str(status).strip().lower())
        query += " GROUP BY p.id ORDER BY p.created_at DESC, p.id DESC LIMIT ?"
        params.append(limit)
        with self.read() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["summary"] = json.loads(value.pop("summary_json"))
            result.append(value)
        return result

    def get_daily_milestone(self, milestone_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_daily_milestones WHERE id = ?",
                (milestone_id.upper(),),
            ).fetchone()
        return self._decode_daily_milestone(row) if row is not None else None

    def list_daily_milestones(
        self,
        *,
        plan_id: str | None = None,
        milestone_date: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        clauses: list[str] = []
        params: list[Any] = []
        if plan_id is not None:
            clauses.append("plan_id = ?")
            params.append(plan_id.upper())
        if milestone_date is not None:
            clauses.append("milestone_date = ?")
            params.append(str(milestone_date))
        if statuses:
            values = tuple(dict.fromkeys(str(value).lower() for value in statuses))
            clauses.append(f"status IN ({','.join('?' for _ in values)})")
            params.extend(values)
        query = "SELECT * FROM planner_daily_milestones"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY milestone_date, allocation_key, id LIMIT ?"
        params.append(limit)
        with self.read() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._decode_daily_milestone(row) for row in rows]

    def set_daily_milestone_status(
        self, milestone_id: str, *, status: str, now: str
    ) -> dict[str, Any] | None:
        normalized = str(status).strip().lower()
        allowed = {
            "draft",
            "planned",
            "in_progress",
            "done",
            "partial",
            "missed",
            "blocked",
            "superseded",
            "cancelled",
        }
        if normalized not in allowed:
            raise ValueError("daily milestone status is invalid")
        milestone_id = milestone_id.upper()
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_daily_milestones SET status = ?, updated_at = ? "
                "WHERE id = ?",
                (normalized, now, milestone_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_daily_milestones WHERE id = ?",
                (milestone_id,),
            ).fetchone()
        return self._decode_daily_milestone(row) if row is not None else None

    def set_daily_milestone_reminder_anchor(
        self,
        milestone_id: str,
        *,
        reminder_anchor: dict[str, Any],
        now: str,
    ) -> dict[str, Any] | None:
        milestone_id = milestone_id.upper()
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_daily_milestones SET reminder_anchor_json = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    json.dumps(reminder_anchor, ensure_ascii=False, sort_keys=True),
                    now,
                    milestone_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_daily_milestones WHERE id = ?",
                (milestone_id,),
            ).fetchone()
        return self._decode_daily_milestone(row) if row is not None else None

    def confirm_milestone_plan(
        self,
        plan_id: str,
        *,
        now: str,
        replace_plan_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Confirm a draft, requiring an exact base-plan match for replacement."""

        plan_id = plan_id.upper()
        requested_replacement = (
            replace_plan_id.upper() if replace_plan_id is not None else None
        )
        with self.write() as conn:
            run = conn.execute(
                "SELECT * FROM planner_milestone_plan_runs WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if run is None:
                return None
            if run["status"] == "confirmed":
                pass
            elif run["status"] != "draft":
                raise ValueError(
                    f"milestone plan {plan_id} cannot be confirmed from "
                    f"status {run['status']}"
                )
            else:
                summary = json.loads(run["summary_json"])
                base_plan_id = summary.get("base_plan_id")
                base_plan_id = (
                    str(base_plan_id).upper() if base_plan_id is not None else None
                )
                if base_plan_id != requested_replacement:
                    raise ValueError(
                        "replace_plan_id must exactly match the draft base_plan_id"
                    )
                if base_plan_id is not None:
                    base = conn.execute(
                        "SELECT status FROM planner_milestone_plan_runs WHERE id = ?",
                        (base_plan_id,),
                    ).fetchone()
                    if base is None or base["status"] != "confirmed":
                        raise ValueError(
                            f"replacement base plan {base_plan_id} is not confirmed"
                        )
                    conn.execute(
                        "UPDATE planner_daily_milestones SET status = 'superseded', "
                        "updated_at = ? WHERE plan_id = ? AND status IN "
                        "('draft', 'planned', 'in_progress')",
                        (now, base_plan_id),
                    )
                    conn.execute(
                        "UPDATE planner_milestone_plan_runs "
                        "SET status = 'superseded', updated_at = ? WHERE id = ?",
                        (now, base_plan_id),
                    )
                conn.execute(
                    "UPDATE planner_milestone_plan_runs "
                    "SET status = 'confirmed', confirmed_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (now, now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_daily_milestones "
                    "SET status = 'planned', updated_at = ? "
                    "WHERE plan_id = ? AND status = 'draft'",
                    (now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_work_packages SET status = 'active', "
                    "updated_at = ? WHERE status = 'proposed' AND id IN "
                    "(SELECT DISTINCT work_package_id FROM planner_daily_milestones "
                    "WHERE plan_id = ? AND work_package_id IS NOT NULL)",
                    (now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_agent_runs SET approval_status = 'approved', "
                    "updated_at = ? WHERE draft_plan_id = ? "
                    "AND approval_status = 'pending'",
                    (now, plan_id),
                )
        return self.get_milestone_plan(plan_id)

    def reject_milestone_plan(
        self, plan_id: str, *, now: str
    ) -> dict[str, Any] | None:
        plan_id = plan_id.upper()
        with self.write() as conn:
            run = conn.execute(
                "SELECT status FROM planner_milestone_plan_runs WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if run is None:
                return None
            if run["status"] == "rejected":
                pass
            elif run["status"] != "draft":
                raise ValueError(
                    f"milestone plan {plan_id} cannot be rejected from "
                    f"status {run['status']}"
                )
            else:
                conn.execute(
                    "UPDATE planner_milestone_plan_runs "
                    "SET status = 'rejected', rejected_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (now, now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_daily_milestones SET status = 'cancelled', "
                    "updated_at = ? WHERE plan_id = ? AND status = 'draft'",
                    (now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_agent_runs SET approval_status = 'rejected', "
                    "updated_at = ? WHERE draft_plan_id = ? "
                    "AND approval_status = 'pending'",
                    (now, plan_id),
                )
        return self.get_milestone_plan(plan_id)

    def save_milestone_checkin(
        self,
        milestone_id: str,
        *,
        outcome: str,
        actual_minutes: int | None,
        completion_fraction: float | None,
        note: str | None,
        now: str,
    ) -> dict[str, Any]:
        milestone_id = milestone_id.upper()
        outcome = str(outcome).strip().lower()
        status_by_outcome = {
            "done": "done",
            "partial": "partial",
            "not_started": "missed",
            "blocked": "blocked",
            "skipped": "missed",
        }
        if outcome not in status_by_outcome:
            raise ValueError("milestone checkin outcome is invalid")
        actual_minutes = 0 if actual_minutes is None else int(actual_minutes)
        completion_fraction = (
            1.0
            if completion_fraction is None and outcome == "done"
            else 0.0
            if completion_fraction is None
            else float(completion_fraction)
        )
        if actual_minutes < 0:
            raise ValueError("actual_minutes must not be negative")
        if not 0.0 <= completion_fraction <= 1.0:
            raise ValueError("completion_fraction must be between 0 and 1")
        checkin_id = _new_id("MC")
        with self.write() as conn:
            milestone = conn.execute(
                "SELECT status FROM planner_daily_milestones WHERE id = ?",
                (milestone_id,),
            ).fetchone()
            if milestone is None:
                raise ValueError(f"milestone {milestone_id} does not exist")
            if milestone["status"] in {"draft", "superseded", "cancelled"}:
                raise ValueError(
                    f"milestone {milestone_id} cannot accept a checkin from "
                    f"status {milestone['status']}"
                )
            conn.execute(
                "INSERT INTO planner_milestone_checkins "
                "(id, milestone_id, outcome, actual_minutes, completion_fraction, "
                "note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(milestone_id) DO UPDATE SET "
                "outcome = excluded.outcome, actual_minutes = excluded.actual_minutes, "
                "completion_fraction = excluded.completion_fraction, "
                "note = excluded.note, updated_at = excluded.updated_at",
                (
                    checkin_id,
                    milestone_id,
                    outcome,
                    actual_minutes,
                    completion_fraction,
                    note,
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE planner_daily_milestones SET status = ?, updated_at = ? "
                "WHERE id = ?",
                (status_by_outcome[outcome], now, milestone_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_milestone_checkins WHERE milestone_id = ?",
                (milestone_id,),
            ).fetchone()
        assert row is not None
        return dict(row)

    def list_milestone_checkins(
        self,
        *,
        milestone_ids: tuple[str, ...] | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM planner_milestone_checkins"
        params: list[Any] = []
        if milestone_ids:
            values = tuple(value.upper() for value in milestone_ids)
            query += f" WHERE milestone_id IN ({','.join('?' for _ in values)})"
            params.extend(values)
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self.read() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def save_estimate_observations(
        self,
        *,
        checkin: dict[str, Any],
        milestone: dict[str, Any],
        observations: list[dict[str, Any]],
        now: str,
    ) -> list[dict[str, Any]]:
        checkin_id = str(checkin["id"]).upper()
        milestone_id = str(milestone["id"]).upper()
        with self.write() as conn:
            for observation in observations:
                conn.execute(
                    "INSERT INTO planner_estimate_observations "
                    "(id, checkin_id, milestone_id, work_package_id, source_step_id, "
                    "allocation_key, estimated_p50_minutes, estimated_p90_minutes, "
                    "credited_p50_minutes, credited_p90_minutes, "
                    "remaining_adjustment_p50_minutes, "
                    "remaining_adjustment_p90_minutes, actual_minutes, "
                    "completion_fraction, outcome, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(checkin_id, source_step_id) DO NOTHING",
                    (
                        _new_id("EO"),
                        checkin_id,
                        milestone_id,
                        str(observation["work_package_id"]).upper(),
                        str(observation["source_step_id"]),
                        str(observation["allocation_key"]),
                        int(observation["estimated_p50_minutes"]),
                        int(observation["estimated_p90_minutes"]),
                        int(observation["credited_p50_minutes"]),
                        int(observation["credited_p90_minutes"]),
                        int(
                            observation.get("remaining_adjustment_p50_minutes") or 0
                        ),
                        int(
                            observation.get("remaining_adjustment_p90_minutes") or 0
                        ),
                        observation.get("actual_minutes"),
                        observation.get("completion_fraction"),
                        str(observation["outcome"]),
                        now,
                    ),
                )
            rows = conn.execute(
                "SELECT o.*, p.domain FROM planner_estimate_observations o "
                "JOIN planner_work_packages p ON p.id = o.work_package_id "
                "WHERE o.checkin_id = ? ORDER BY o.source_step_id, o.id",
                (checkin_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_estimate_observations(
        self, *, work_package_ids: tuple[str, ...] | None = None
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT o.*, p.domain FROM planner_estimate_observations o "
            "JOIN planner_work_packages p ON p.id = o.work_package_id"
        )
        params: list[Any] = []
        if work_package_ids:
            values = tuple(value.upper() for value in work_package_ids)
            query += f" WHERE o.work_package_id IN ({','.join('?' for _ in values)})"
            params.extend(values)
        query += " ORDER BY o.created_at, o.id"
        with self.read() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def claim_milestone_reminder_batch(
        self,
        *,
        batch_id: str,
        reminders: list[dict[str, Any]],
        emitted_at: str,
        delivery_target: str,
    ) -> list[dict[str, Any]]:
        """Claim logical reminders before stdout is handed to Cron delivery."""

        batch_id = str(batch_id).strip()
        if not batch_id:
            raise ValueError("reminder batch_id is required")
        target = str(delivery_target or "local").strip()
        with self.write() as conn:
            for reminder in reminders:
                conn.execute(
                    "INSERT INTO planner_milestone_reminder_deliveries "
                    "(id, milestone_id, dedupe_key, batch_id, scheduled_for, "
                    "status, emitted_at, delivered_at, delivery_target, "
                    "attempt_count, last_error, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, ?, 1, NULL, ?) "
                    "ON CONFLICT(dedupe_key) DO UPDATE SET "
                    "batch_id = excluded.batch_id, status = 'pending', "
                    "emitted_at = excluded.emitted_at, delivered_at = NULL, "
                    "delivery_target = excluded.delivery_target, "
                    "attempt_count = planner_milestone_reminder_deliveries.attempt_count + 1, "
                    "last_error = NULL WHERE "
                    "planner_milestone_reminder_deliveries.status = 'retryable'",
                    (
                        _new_id("MR"),
                        str(reminder["milestone_id"]).upper(),
                        str(reminder["dedupe_key"]),
                        batch_id,
                        str(reminder["scheduled_for"]),
                        emitted_at,
                        target,
                        emitted_at,
                    ),
                )
            rows = conn.execute(
                "SELECT * FROM planner_milestone_reminder_deliveries "
                "WHERE batch_id = ? AND status = 'pending' "
                "ORDER BY scheduled_for, id",
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def reconcile_milestone_reminder_batch(
        self,
        *,
        batch_id: str,
        delivered: bool,
        error: str | None,
        reconciled_at: str,
    ) -> list[dict[str, Any]]:
        status = "sent" if delivered else "retryable"
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_milestone_reminder_deliveries "
                "SET status = ?, delivered_at = ?, last_error = ? "
                "WHERE batch_id = ? AND status = 'pending'",
                (
                    status,
                    reconciled_at if delivered else None,
                    None if delivered else str(error or "delivery failed")[:500],
                    str(batch_id),
                ),
            )
            rows = conn.execute(
                "SELECT * FROM planner_milestone_reminder_deliveries "
                "WHERE batch_id = ? ORDER BY scheduled_for, id",
                (str(batch_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_pending_milestone_reminder_batches(self) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT batch_id, 'pending' AS status, MIN(emitted_at) AS emitted_at, "
                "COUNT(*) AS reminder_count FROM planner_milestone_reminder_deliveries "
                "WHERE status = 'pending' GROUP BY batch_id "
                "ORDER BY emitted_at, batch_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_milestone_reminder_deliveries(
        self,
        *,
        milestone_ids: tuple[str, ...] | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if milestone_ids:
            values = tuple(value.upper() for value in milestone_ids)
            clauses.append(f"milestone_id IN ({','.join('?' for _ in values)})")
            params.extend(values)
        if since is not None:
            clauses.append("emitted_at >= ?")
            params.append(since)
        query = "SELECT * FROM planner_milestone_reminder_deliveries"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY emitted_at DESC, id DESC"
        with self.read() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def work_package_remaining_summary(
        self, *, work_package_ids: tuple[str, ...]
    ) -> dict[str, Any]:
        if not work_package_ids:
            return {"p50_minutes": 0, "p90_minutes": 0, "work_package_count": 0}
        values = tuple(dict.fromkeys(value.upper() for value in work_package_ids))
        placeholders = ",".join("?" for _ in values)
        with self.read() as conn:
            packages = conn.execute(
                f"SELECT id, p50_minutes, p90_minutes FROM planner_work_packages "
                f"WHERE id IN ({placeholders})",
                values,
            ).fetchall()
            observations = conn.execute(
                "SELECT work_package_id, "
                "SUM(credited_p50_minutes) AS credited_p50, "
                "SUM(credited_p90_minutes) AS credited_p90, "
                "SUM(remaining_adjustment_p50_minutes) AS adjustment_p50, "
                "SUM(remaining_adjustment_p90_minutes) AS adjustment_p90 "
                f"FROM planner_estimate_observations WHERE work_package_id IN "
                f"({placeholders}) GROUP BY work_package_id",
                values,
            ).fetchall()
        progress = {row["work_package_id"]: dict(row) for row in observations}
        per_package = []
        for package in packages:
            observed = progress.get(package["id"], {})
            p50 = max(
                0,
                int(package["p50_minutes"])
                - int(observed.get("credited_p50") or 0)
                + int(observed.get("adjustment_p50") or 0),
            )
            p90 = max(
                0,
                int(package["p90_minutes"])
                - int(observed.get("credited_p90") or 0)
                + int(observed.get("adjustment_p90") or 0),
            )
            per_package.append(
                {"work_package_id": package["id"], "p50_minutes": p50, "p90_minutes": p90}
            )
        return {
            "p50_minutes": sum(value["p50_minutes"] for value in per_package),
            "p90_minutes": sum(value["p90_minutes"] for value in per_package),
            "work_package_count": len(per_package),
            "per_package": per_package,
        }

    def save_agent_run(
        self,
        *,
        trigger_event_id: str | None,
        selected_experts: Iterable[str],
        expert_result_ids: Iterable[str],
        decision: dict[str, Any],
        draft_plan_id: str | None,
        approval_status: str,
        outcome: str,
        reasoning_summary: str,
        usage: dict[str, Any] | None,
        now: str,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        run_id = _new_id("AR")
        selected = list(dict.fromkeys(str(value) for value in selected_experts))
        expert_ids = list(
            dict.fromkeys(str(value).upper() for value in expert_result_ids)
        )
        with self.write() as conn:
            conn.execute(
                "INSERT INTO planner_agent_runs "
                "(id, trigger_event_id, selected_experts_json, "
                "expert_result_ids_json, decision_json, draft_plan_id, "
                "approval_status, outcome, reasoning_summary, usage_json, "
                "created_at, updated_at, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    trigger_event_id.upper() if trigger_event_id else None,
                    json.dumps(selected, ensure_ascii=False, sort_keys=True),
                    json.dumps(expert_ids, ensure_ascii=False, sort_keys=True),
                    json.dumps(decision, ensure_ascii=False, sort_keys=True),
                    draft_plan_id.upper() if draft_plan_id else None,
                    approval_status,
                    outcome,
                    reasoning_summary,
                    json.dumps(usage or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    duration_ms,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        return self._decode_agent_run(row)

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_agent_runs WHERE id = ?", (run_id.upper(),)
            ).fetchone()
        return self._decode_agent_run(row) if row is not None else None

    def list_agent_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM planner_agent_runs "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._decode_agent_run(row) for row in rows]

    def create_item(self, values: dict[str, Any]) -> dict[str, Any]:
        item_id = values.get("id") or _new_id("P")
        record = {**values, "id": item_id}
        record.setdefault("content_hash", content_hash(record.get("raw_content") or ""))
        record.setdefault(
            "source_key",
            source_key(
                source_type=record.get("source_type") or "manual",
                source_uri=record.get("source_uri"),
                raw_content=record.get("raw_content") or "",
            ),
        )
        record.setdefault("event_duration_minutes", 60)
        columns = (
            "id", "title", "raw_content", "source_type", "source_uri",
            "source_key", "content_hash",
            "discovered_at", "actionable_at", "due_at", "event_at",
            "timezone", "importance", "status", "acknowledged_at",
            "completed_at", "snoozed_until", "event_duration_minutes",
            "created_at", "updated_at",
        )
        placeholders = ", ".join("?" for _ in columns)
        with self.write() as conn:
            conn.execute(
                f"INSERT INTO planner_items ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(record.get(column) for column in columns),
            )
            row = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (item_id,)
            ).fetchone()
        return dict(row)

    def capture_item(self, values: dict[str, Any]) -> dict[str, Any]:
        """Atomically create, deduplicate, or revise a notification."""
        values = dict(values)
        provided_fields = set(values.pop("_provided_fields", ()))
        identity = values["source_key"]
        digest = values["content_hash"]
        with self.write() as conn:
            active = conn.execute(
                "SELECT * FROM planner_items WHERE source_key = ? "
                "AND status IN ('draft', 'confirmed') "
                "ORDER BY created_at DESC LIMIT 1",
                (identity,),
            ).fetchone()
            if active is None:
                exact = conn.execute(
                    "SELECT * FROM planner_items WHERE content_hash = ? "
                    "AND status IN ('draft', 'confirmed') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (digest,),
                ).fetchone()
                if exact is not None:
                    return {**dict(exact), "capture_outcome": "duplicate"}
                item_id = values.get("id") or _new_id("P")
                self._insert_item(conn, {**values, "id": item_id})
                created = conn.execute(
                    "SELECT * FROM planner_items WHERE id = ?", (item_id,)
                ).fetchone()
                return {**dict(created), "capture_outcome": "created"}

            tracked_fields = (
                "title",
                "raw_content",
                "source_type",
                "source_uri",
                "source_key",
                "content_hash",
                "actionable_at",
                "due_at",
                "event_at",
                "timezone",
                "importance",
                "event_duration_minutes",
            )
            effective = dict(values)
            if active["content_hash"] == digest:
                effective["raw_content"] = active["raw_content"]
                if "title" not in provided_fields:
                    effective["title"] = active["title"]
            for field in (
                "actionable_at",
                "due_at",
                "event_at",
                "timezone",
                "importance",
                "event_duration_minutes",
            ):
                if field not in provided_fields:
                    effective[field] = active[field]
            changed = {
                field: {"from": active[field], "to": effective.get(field)}
                for field in tracked_fields
                if active[field] != effective.get(field)
            }
            if not changed:
                return {**dict(active), "capture_outcome": "duplicate"}

            conn.execute(
                "INSERT INTO planner_item_revisions "
                "(id, item_id, previous_json, changed_fields, observed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    _new_id("R"),
                    active["id"],
                    json.dumps(dict(active), ensure_ascii=False, sort_keys=True),
                    json.dumps(changed, ensure_ascii=False, sort_keys=True),
                    effective["updated_at"],
                ),
            )
            assignments = ", ".join(f"{field} = ?" for field in tracked_fields)
            conn.execute(
                f"UPDATE planner_items SET {assignments}, status = 'draft', "
                "acknowledged_at = NULL, snoozed_until = NULL, updated_at = ? "
                "WHERE id = ?",
                (
                    *(effective.get(field) for field in tracked_fields),
                    effective["updated_at"],
                    active["id"],
                ),
            )
            conn.execute(
                "UPDATE planner_nudges SET status = 'cancelled' "
                "WHERE item_id = ? AND status = 'pending'",
                (active["id"],),
            )
            conn.execute(
                "UPDATE planner_calendar_links SET status = 'stale', updated_at = ? "
                "WHERE item_id = ? AND status = 'active'",
                (effective["updated_at"], active["id"]),
            )
            updated = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (active["id"],)
            ).fetchone()
            return {**dict(updated), "capture_outcome": "updated"}

    @staticmethod
    def _insert_item(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        columns = (
            "id", "title", "raw_content", "source_type", "source_uri",
            "source_key", "content_hash", "discovered_at", "actionable_at",
            "due_at", "event_at", "timezone", "importance", "status",
            "acknowledged_at", "completed_at", "snoozed_until",
            "event_duration_minutes", "created_at", "updated_at",
        )
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO planner_items ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(record.get(column) for column in columns),
        )

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            return self._dict(
                conn.execute(
                    "SELECT * FROM planner_items WHERE id = ?", (item_id.upper(),)
                ).fetchone()
            )

    def list_items(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM planner_items WHERE status = ? "
                    "ORDER BY COALESCE(due_at, event_at, actionable_at, created_at), created_at "
                    "LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM planner_items "
                    "ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, "
                    "COALESCE(due_at, event_at, actionable_at, created_at), created_at LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def confirm_item(
        self,
        item_id: str,
        *,
        updated_at: str,
        nudges: Iterable[dict[str, Any]],
    ) -> dict[str, Any] | None:
        item_id = item_id.upper()
        with self.write() as conn:
            changed = conn.execute(
                "UPDATE planner_items SET status = 'confirmed', updated_at = ? "
                "WHERE id = ? AND status = 'draft'",
                (updated_at, item_id),
            ).rowcount
            current = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (item_id,)
            ).fetchone()
            if current is None:
                return None
            if not changed and current["status"] != "confirmed":
                raise ValueError(
                    f"item {item_id} cannot be confirmed from status {current['status']}"
                )
            for nudge in nudges:
                conn.execute(
                    "INSERT INTO planner_nudges "
                    "(id, item_id, kind, fire_at, severity, ack_required, status, "
                    "sent_at, acknowledged_at, idempotency_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?) "
                    "ON CONFLICT(idempotency_key) DO UPDATE SET "
                    "kind = excluded.kind, fire_at = excluded.fire_at, "
                    "severity = excluded.severity, ack_required = excluded.ack_required, "
                    "status = 'pending', sent_at = NULL, acknowledged_at = NULL, "
                    "created_at = excluded.created_at",
                    (
                        nudge.get("id") or _new_id("N"), item_id, nudge["kind"],
                        nudge["fire_at"], nudge["severity"],
                        1 if nudge.get("ack_required") else 0,
                        nudge["idempotency_key"], nudge["created_at"],
                    ),
                )
            row = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (item_id,)
            ).fetchone()
        return dict(row)

    def list_nudges(self, item_id: str) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM planner_nudges WHERE item_id = ? ORDER BY fire_at, id",
                (item_id.upper(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_revisions(self, item_id: str) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM planner_item_revisions WHERE item_id = ? "
                "ORDER BY observed_at, id",
                (item_id.upper(),),
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["previous"] = json.loads(value.pop("previous_json"))
            value["changes"] = json.loads(value.pop("changed_fields"))
            result.append(value)
        return result

    def list_calendar_links(self, item_id: str) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM planner_calendar_links WHERE item_id = ? "
                "ORDER BY event_kind, created_at",
                (item_id.upper(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def reserve_calendar_link(
        self,
        *,
        item_id: str,
        provider: str,
        calendar_id: str,
        event_kind: str,
        payload_hash: str,
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        with self.write() as conn:
            inserted = conn.execute(
                "INSERT INTO planner_calendar_links "
                "(id, item_id, provider, calendar_id, event_kind, external_event_id, "
                "html_link, payload_hash, status, last_error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 'creating', NULL, ?, ?) "
                "ON CONFLICT(item_id, provider, calendar_id, event_kind) DO NOTHING",
                (
                    _new_id("C"),
                    item_id.upper(),
                    provider,
                    calendar_id,
                    event_kind,
                    payload_hash,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_calendar_links WHERE item_id = ? "
                "AND provider = ? AND calendar_id = ? AND event_kind = ?",
                (item_id.upper(), provider, calendar_id, event_kind),
            ).fetchone()
        return dict(row), bool(inserted.rowcount)

    def complete_calendar_link(
        self,
        *,
        link_id: str,
        external_event_id: str,
        html_link: str | None,
        now: str,
    ) -> dict[str, Any]:
        with self.write() as conn:
            changed = conn.execute(
                "UPDATE planner_calendar_links SET external_event_id = ?, "
                "html_link = ?, status = 'active', last_error = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'creating'",
                (external_event_id, html_link, now, link_id),
            ).rowcount
            if not changed:
                raise RuntimeError(
                    f"calendar link {link_id} is no longer reserved for creation"
                )
            row = conn.execute(
                "SELECT * FROM planner_calendar_links WHERE id = ?", (link_id,)
            ).fetchone()
        return dict(row)

    def fail_calendar_link(self, *, link_id: str, error: str, now: str) -> None:
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_calendar_links SET status = 'failed', "
                "last_error = ?, updated_at = ? WHERE id = ? AND status = 'creating'",
                (str(error)[:1000], now, link_id),
            )

    def delete_calendar_links(
        self,
        *,
        item_id: str,
        provider: str,
        calendar_id: str,
        event_kinds: Iterable[str],
    ) -> list[dict[str, Any]]:
        kinds = tuple(dict.fromkeys(str(kind) for kind in event_kinds))
        if not kinds:
            return []
        placeholders = ", ".join("?" for _ in kinds)
        with self.write() as conn:
            rows = conn.execute(
                "SELECT * FROM planner_calendar_links WHERE item_id = ? "
                "AND provider = ? AND calendar_id = ? "
                f"AND event_kind IN ({placeholders})",
                (item_id.upper(), provider, calendar_id, *kinds),
            ).fetchall()
            conn.execute(
                "DELETE FROM planner_calendar_links WHERE item_id = ? "
                "AND provider = ? AND calendar_id = ? "
                f"AND event_kind IN ({placeholders})",
                (item_id.upper(), provider, calendar_id, *kinds),
            )
        return [dict(row) for row in rows]

    def replace_calendar_busy_cache(
        self,
        *,
        provider: str,
        calendar_id: str,
        window_start: str,
        window_end: str,
        events: Iterable[dict[str, Any]],
        fetched_at: str,
        expires_at: str,
        snapshot_hash: str,
    ) -> list[dict[str, Any]]:
        records = list(events)
        with self.write() as conn:
            conn.execute(
                "DELETE FROM planner_calendar_busy_cache "
                "WHERE provider = ? AND calendar_id = ? "
                "AND ends_at > ? AND starts_at < ?",
                (provider, calendar_id, window_start, window_end),
            )
            for event in records:
                conn.execute(
                    "INSERT INTO planner_calendar_busy_cache "
                    "(id, provider, calendar_id, event_id, summary, starts_at, "
                    "ends_at, all_day, status, html_link, raw_json, fetched_at, "
                    "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(provider, calendar_id, event_id) DO UPDATE SET "
                    "summary = excluded.summary, starts_at = excluded.starts_at, "
                    "ends_at = excluded.ends_at, all_day = excluded.all_day, "
                    "status = excluded.status, html_link = excluded.html_link, "
                    "raw_json = excluded.raw_json, fetched_at = excluded.fetched_at, "
                    "expires_at = excluded.expires_at",
                    (
                        _new_id("E"),
                        provider,
                        calendar_id,
                        event["event_id"],
                        event["summary"],
                        event["starts_at"],
                        event["ends_at"],
                        1 if event.get("all_day") else 0,
                        event.get("status") or "confirmed",
                        event.get("html_link"),
                        json.dumps(event.get("raw") or {}, ensure_ascii=False),
                        fetched_at,
                        expires_at,
                    ),
                )
            conn.execute(
                "INSERT INTO planner_calendar_sync_state "
                "(id, provider, calendar_id, window_start, window_end, "
                "event_count, snapshot_hash, fetched_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, calendar_id, window_start, window_end) "
                "DO UPDATE SET event_count = excluded.event_count, "
                "snapshot_hash = excluded.snapshot_hash, "
                "fetched_at = excluded.fetched_at, "
                "expires_at = excluded.expires_at",
                (
                    _new_id("Q"),
                    provider,
                    calendar_id,
                    window_start,
                    window_end,
                    len(records),
                    snapshot_hash,
                    fetched_at,
                    expires_at,
                ),
            )
        return self.list_cached_calendar_busy(
            provider=provider,
            calendar_ids=(calendar_id,),
            window_start=window_start,
            window_end=window_end,
        )

    def list_cached_calendar_busy(
        self,
        *,
        provider: str,
        calendar_ids: Iterable[str],
        window_start: str,
        window_end: str,
    ) -> list[dict[str, Any]]:
        calendars = tuple(dict.fromkeys(str(value) for value in calendar_ids))
        if not calendars:
            return []
        placeholders = ", ".join("?" for _ in calendars)
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM planner_calendar_busy_cache "
                "WHERE provider = ? "
                f"AND calendar_id IN ({placeholders}) "
                "AND ends_at > ? AND starts_at < ? "
                "ORDER BY starts_at, calendar_id, event_id",
                (provider, *calendars, window_start, window_end),
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["raw"] = json.loads(value.pop("raw_json"))
            result.append(value)
        return result

    def get_calendar_sync_state(
        self,
        *,
        provider: str,
        calendar_id: str,
        window_start: str,
        window_end: str,
    ) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_calendar_sync_state "
                "WHERE provider = ? AND calendar_id = ? "
                "AND window_start = ? AND window_end = ?",
                (provider, calendar_id, window_start, window_end),
            ).fetchone()
        return self._dict(row)

    def create_goal(self, values: dict[str, Any]) -> dict[str, Any]:
        goal_id = values.get("id") or _new_id("G")
        columns = (
            "id",
            "title",
            "area",
            "outcome",
            "target_date",
            "weekly_target_minutes",
            "priority",
            "status",
            "created_at",
            "updated_at",
            "completed_at",
        )
        record = {**values, "id": goal_id}
        with self.write() as conn:
            conn.execute(
                f"INSERT INTO planner_goals ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(record.get(column) for column in columns),
            )
            row = conn.execute(
                "SELECT * FROM planner_goals WHERE id = ?", (goal_id,)
            ).fetchone()
        return dict(row)

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            return self._dict(
                conn.execute(
                    "SELECT * FROM planner_goals WHERE id = ?", (goal_id.upper(),)
                ).fetchone()
            )

    def list_goals(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM planner_goals WHERE status = ? "
                    "ORDER BY COALESCE(target_date, '9999'), priority DESC, created_at "
                    "LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM planner_goals "
                    "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, "
                    "COALESCE(target_date, '9999'), created_at LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def set_goal_status(
        self, goal_id: str, *, status: str, now: str
    ) -> dict[str, Any] | None:
        completed_at = now if status == "done" else None
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_goals SET status = ?, completed_at = ?, updated_at = ? "
                "WHERE id = ?",
                (status, completed_at, now, goal_id.upper()),
            )
            row = conn.execute(
                "SELECT * FROM planner_goals WHERE id = ?", (goal_id.upper(),)
            ).fetchone()
        return self._dict(row)

    def create_task(self, values: dict[str, Any]) -> dict[str, Any]:
        task_id = values.get("id") or _new_id("T")
        columns = (
            "id",
            "goal_id",
            "planner_item_id",
            "title",
            "notes",
            "earliest_start",
            "due_at",
            "estimated_minutes",
            "completed_minutes",
            "min_block_minutes",
            "splittable",
            "energy",
            "priority",
            "status",
            "created_at",
            "updated_at",
            "completed_at",
        )
        record = {**values, "id": task_id}
        with self.write() as conn:
            conn.execute(
                f"INSERT INTO planner_tasks ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(record.get(column) for column in columns),
            )
            row = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return dict(row)

    def update_task(
        self,
        task_id: str,
        *,
        values: dict[str, Any],
        now: str,
    ) -> dict[str, Any] | None:
        allowed = {
            "goal_id",
            "title",
            "notes",
            "earliest_start",
            "due_at",
            "estimated_minutes",
            "min_block_minutes",
            "splittable",
            "energy",
            "priority",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return self.get_task(task_id)
        task_id = task_id.upper()
        with self.write() as conn:
            current = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if current is None:
                return None
            if current["status"] != "backlog":
                raise ValueError(
                    f"task {task_id} can only be edited while it is in backlog"
                )
            assignments = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"UPDATE planner_tasks SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), now, task_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._dict(row)

    def cancel_task(self, task_id: str, *, now: str) -> dict[str, Any] | None:
        task_id = task_id.upper()
        with self.write() as conn:
            current = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if current is None:
                return None
            if current["status"] == "cancelled":
                return dict(current)
            if current["status"] == "done":
                raise ValueError(f"completed task {task_id} cannot be cancelled")
            future_rows = conn.execute(
                "SELECT id FROM planner_plan_blocks WHERE task_id = ? "
                "AND status IN ('proposed', 'confirmed')",
                (task_id,),
            ).fetchall()
            future_ids = [row["id"] for row in future_rows]
            if future_ids:
                placeholders = ", ".join("?" for _ in future_ids)
                conn.execute(
                    f"UPDATE planner_plan_block_calendar_links "
                    f"SET status = 'stale', updated_at = ? "
                    f"WHERE block_id IN ({placeholders}) AND status = 'active'",
                    (now, *future_ids),
                )
            conn.execute(
                "UPDATE planner_plan_blocks SET status = 'skipped', updated_at = ? "
                "WHERE task_id = ? AND status IN ('proposed', 'confirmed')",
                (now, task_id),
            )
            conn.execute(
                "UPDATE planner_tasks SET status = 'cancelled', completed_at = NULL, "
                "updated_at = ? WHERE id = ?",
                (now, task_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._dict(row)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            return self._dict(
                conn.execute(
                    "SELECT * FROM planner_tasks WHERE id = ?", (task_id.upper(),)
                ).fetchone()
            )

    def list_tasks(
        self,
        *,
        status: str | None = None,
        goal_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("t.status = ?")
            params.append(status)
        if goal_id:
            clauses.append("t.goal_id = ?")
            params.append(goal_id.upper())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.read() as conn:
            rows = conn.execute(
                "SELECT t.*, g.title AS goal_title, g.priority AS goal_priority, "
                "g.weekly_target_minutes AS goal_weekly_target_minutes "
                "FROM planner_tasks t "
                "LEFT JOIN planner_goals g ON g.id = t.goal_id "
                f"{where} "
                "ORDER BY CASE t.status WHEN 'backlog' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END, "
                "COALESCE(t.due_at, '9999'), t.created_at LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_task_progress(
        self,
        task_id: str,
        *,
        minutes: int,
        now: str,
        source: str = "manual",
        note: str | None = None,
    ) -> dict[str, Any] | None:
        task_id = task_id.upper()
        with self.write() as conn:
            row = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            completed = min(
                int(row["estimated_minutes"]),
                int(row["completed_minutes"]) + int(minutes),
            )
            recorded_minutes = completed - int(row["completed_minutes"])
            done = completed >= int(row["estimated_minutes"])
            conn.execute(
                "UPDATE planner_tasks SET completed_minutes = ?, status = ?, "
                "completed_at = ?, updated_at = ? WHERE id = ?",
                (
                    completed,
                    "done" if done else row["status"],
                    now if done else row["completed_at"],
                    now,
                    task_id,
                ),
            )
            if recorded_minutes > 0:
                conn.execute(
                    "INSERT INTO planner_task_progress_log "
                    "(id, task_id, minutes, source, note, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _new_id("L"),
                        task_id,
                        recorded_minutes,
                        str(source or "manual")[:80],
                        str(note)[:1000] if note else None,
                        now,
                    ),
                )
            if done:
                future_rows = conn.execute(
                    "SELECT id FROM planner_plan_blocks "
                    "WHERE task_id = ? AND status = 'confirmed' AND ends_at > ?",
                    (task_id, now),
                ).fetchall()
                future_ids = [value["id"] for value in future_rows]
                if future_ids:
                    placeholders = ", ".join("?" for _ in future_ids)
                    conn.execute(
                        f"UPDATE planner_plan_block_calendar_links "
                        f"SET status = 'stale', updated_at = ? "
                        f"WHERE block_id IN ({placeholders}) "
                        "AND status = 'active'",
                        (now, *future_ids),
                    )
                conn.execute(
                    "UPDATE planner_plan_blocks SET status = 'done', updated_at = ? "
                    "WHERE task_id = ? AND status = 'confirmed' AND ends_at > ?",
                    (now, task_id, now),
                )
            updated = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return dict(updated)

    def complete_task(
        self,
        task_id: str,
        *,
        now: str,
        source: str = "manual",
        note: str | None = None,
    ) -> dict[str, Any] | None:
        with self.write() as conn:
            current = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id.upper(),)
            ).fetchone()
            if current is None:
                return None
            recorded_minutes = max(
                0,
                int(current["estimated_minutes"])
                - int(current["completed_minutes"]),
            )
            conn.execute(
                "UPDATE planner_tasks SET completed_minutes = estimated_minutes, "
                "status = 'done', completed_at = ?, updated_at = ? WHERE id = ? "
                "AND status IN ('backlog', 'planned')",
                (now, now, task_id.upper()),
            )
            future_rows = conn.execute(
                "SELECT id FROM planner_plan_blocks "
                "WHERE task_id = ? AND status = 'confirmed' AND ends_at > ?",
                (task_id.upper(), now),
            ).fetchall()
            future_ids = [value["id"] for value in future_rows]
            if future_ids:
                placeholders = ", ".join("?" for _ in future_ids)
                conn.execute(
                    f"UPDATE planner_plan_block_calendar_links "
                    f"SET status = 'stale', updated_at = ? "
                    f"WHERE block_id IN ({placeholders}) AND status = 'active'",
                    (now, *future_ids),
                )
            conn.execute(
                "UPDATE planner_plan_blocks SET status = 'done', updated_at = ? "
                "WHERE task_id = ? AND status = 'confirmed' AND ends_at > ?",
                (now, task_id.upper(), now),
            )
            if recorded_minutes > 0:
                conn.execute(
                    "INSERT INTO planner_task_progress_log "
                    "(id, task_id, minutes, source, note, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _new_id("L"),
                        task_id.upper(),
                        recorded_minutes,
                        str(source or "manual")[:80],
                        str(note)[:1000] if note else None,
                        now,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id.upper(),)
            ).fetchone()
        return self._dict(row)

    def list_task_progress(
        self, *, window_start: str, window_end: str
    ) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT l.*, t.title, t.goal_id FROM planner_task_progress_log l "
                "JOIN planner_tasks t ON t.id = l.task_id "
                "WHERE l.recorded_at >= ? AND l.recorded_at < ? "
                "ORDER BY l.recorded_at, l.id",
                (window_start, window_end),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_availability(self, values: dict[str, Any]) -> dict[str, Any]:
        availability_id = values.get("id") or _new_id("A")
        columns = (
            "id",
            "weekday",
            "start_minute",
            "end_minute",
            "energy",
            "label",
            "enabled",
            "created_at",
            "updated_at",
        )
        record = {**values, "id": availability_id}
        with self.write() as conn:
            conn.execute(
                f"INSERT INTO planner_availability ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(record.get(column) for column in columns),
            )
            row = conn.execute(
                "SELECT * FROM planner_availability WHERE id = ?",
                (availability_id,),
            ).fetchone()
        return dict(row)

    def list_availability(self, *, weekday: int | None = None) -> list[dict[str, Any]]:
        with self.read() as conn:
            if weekday is None:
                rows = conn.execute(
                    "SELECT * FROM planner_availability "
                    "ORDER BY weekday, start_minute, end_minute"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM planner_availability WHERE weekday = ? "
                    "ORDER BY start_minute, end_minute",
                    (weekday,),
                ).fetchall()
        return [dict(row) for row in rows]

    def delete_availability(
        self, *, availability_id: str | None = None, weekday: int | None = None
    ) -> list[dict[str, Any]]:
        if availability_id is None and weekday is None:
            raise ValueError("availability_id or weekday is required")
        with self.write() as conn:
            if availability_id is not None:
                rows = conn.execute(
                    "SELECT * FROM planner_availability WHERE id = ?",
                    (availability_id.upper(),),
                ).fetchall()
                conn.execute(
                    "DELETE FROM planner_availability WHERE id = ?",
                    (availability_id.upper(),),
                )
            else:
                rows = conn.execute(
                    "SELECT * FROM planner_availability WHERE weekday = ?",
                    (weekday,),
                ).fetchall()
                conn.execute(
                    "DELETE FROM planner_availability WHERE weekday = ?",
                    (weekday,),
                )
        return [dict(row) for row in rows]

    def list_confirmed_plan_blocks(
        self,
        *,
        window_start: str,
        window_end: str,
        exclude_plan_ids: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        excluded = tuple(dict.fromkeys(str(value) for value in exclude_plan_ids))
        exclusion = ""
        params: list[Any] = [window_start, window_end]
        if excluded:
            exclusion = (
                f"AND b.plan_id NOT IN ({', '.join('?' for _ in excluded)}) "
            )
            params.extend(excluded)
        with self.read() as conn:
            rows = conn.execute(
                "SELECT b.*, r.timezone, r.status AS plan_status "
                "FROM planner_plan_blocks b "
                "JOIN planner_plan_runs r ON r.id = b.plan_id "
                "WHERE r.status = 'confirmed' AND b.status = 'confirmed' "
                "AND b.ends_at > ? AND b.starts_at < ? "
                f"{exclusion}"
                "ORDER BY b.starts_at, b.id",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_plan_blocks_for_window(
        self,
        *,
        window_start: str,
        window_end: str,
        plan_status: str = "confirmed",
    ) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT b.*, r.status AS plan_status, r.timezone, "
                "t.title, t.goal_id, t.status AS task_status, t.due_at, "
                "t.estimated_minutes, t.completed_minutes "
                "FROM planner_plan_blocks b "
                "JOIN planner_plan_runs r ON r.id = b.plan_id "
                "JOIN planner_tasks t ON t.id = b.task_id "
                "WHERE r.status = ? AND b.ends_at > ? AND b.starts_at < ? "
                "ORDER BY b.starts_at, b.id",
                (plan_status, window_start, window_end),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_plan_block(self, block_id: str) -> dict[str, Any] | None:
        block_id = block_id.upper()
        with self.read() as conn:
            row = conn.execute(
                "SELECT b.*, r.status AS plan_status, r.timezone, "
                "t.title, t.goal_id, t.status AS task_status, t.due_at, "
                "t.estimated_minutes, t.completed_minutes "
                "FROM planner_plan_blocks b "
                "JOIN planner_plan_runs r ON r.id = b.plan_id "
                "JOIN planner_tasks t ON t.id = b.task_id "
                "WHERE b.id = ?",
                (block_id,),
            ).fetchone()
            if row is None:
                return None
            checkin = conn.execute(
                "SELECT * FROM planner_block_checkins WHERE block_id = ?",
                (block_id,),
            ).fetchone()
            links = conn.execute(
                "SELECT * FROM planner_plan_block_calendar_links "
                "WHERE block_id = ? ORDER BY calendar_id, created_at",
                (block_id,),
            ).fetchall()
        result = dict(row)
        result["checkin"] = self._dict(checkin)
        result["calendar_links"] = [dict(link) for link in links]
        return result

    def list_block_checkins(
        self, *, window_start: str, window_end: str
    ) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT c.*, b.plan_id, b.starts_at, b.ends_at, t.title "
                "FROM planner_block_checkins c "
                "JOIN planner_plan_blocks b ON b.id = c.block_id "
                "JOIN planner_tasks t ON t.id = c.task_id "
                "WHERE c.recorded_at >= ? AND c.recorded_at < ? "
                "ORDER BY c.recorded_at, c.id",
                (window_start, window_end),
            ).fetchall()
        return [dict(row) for row in rows]

    def checkin_plan_block(
        self,
        *,
        block_id: str,
        outcome: str,
        actual_minutes: int | None,
        note: str | None,
        now: str,
    ) -> dict[str, Any] | None:
        block_id = block_id.upper()
        with self.write() as conn:
            block = conn.execute(
                "SELECT b.*, r.status AS plan_status, t.status AS task_status, "
                "t.estimated_minutes, t.completed_minutes, t.completed_at "
                "FROM planner_plan_blocks b "
                "JOIN planner_plan_runs r ON r.id = b.plan_id "
                "JOIN planner_tasks t ON t.id = b.task_id "
                "WHERE b.id = ?",
                (block_id,),
            ).fetchone()
            if block is None:
                return None
            existing = conn.execute(
                "SELECT * FROM planner_block_checkins WHERE block_id = ?",
                (block_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["outcome"] == outcome
                    and existing["actual_minutes"] == actual_minutes
                ):
                    return self.get_plan_block(block_id)
                raise ValueError(
                    f"block {block_id} was already checked in as "
                    f"{existing['outcome']}"
                )
            if block["plan_status"] != "confirmed" or block["status"] != "confirmed":
                raise ValueError(
                    f"block {block_id} cannot be checked in from "
                    f"{block['plan_status']}/{block['status']}"
                )

            credited = 0
            next_block_status = "done" if outcome == "done" else "skipped"
            if outcome == "done":
                assert actual_minutes is not None
                remaining = max(
                    0,
                    int(block["estimated_minutes"])
                    - int(block["completed_minutes"]),
                )
                credited = min(actual_minutes, remaining)
                completed = int(block["completed_minutes"]) + credited
                task_done = completed >= int(block["estimated_minutes"])
                conn.execute(
                    "UPDATE planner_tasks SET completed_minutes = ?, status = ?, "
                    "completed_at = ?, updated_at = ? WHERE id = ?",
                    (
                        completed,
                        "done" if task_done else block["task_status"],
                        now if task_done else block["completed_at"],
                        now,
                        block["task_id"],
                    ),
                )
                if credited > 0:
                    conn.execute(
                        "INSERT INTO planner_task_progress_log "
                        "(id, task_id, minutes, source, note, recorded_at) "
                        "VALUES (?, ?, ?, 'plan_block', ?, ?)",
                        (
                            _new_id("L"),
                            block["task_id"],
                            credited,
                            str(note)[:1000] if note else f"Block {block_id} done",
                            now,
                        ),
                    )
                if block["ends_at"] > now:
                    conn.execute(
                        "UPDATE planner_plan_block_calendar_links "
                        "SET status = 'stale', updated_at = ? "
                        "WHERE block_id = ? AND status = 'active'",
                        (now, block_id),
                    )
                if task_done:
                    future_rows = conn.execute(
                        "SELECT id FROM planner_plan_blocks "
                        "WHERE task_id = ? AND id != ? AND status = 'confirmed' "
                        "AND ends_at > ?",
                        (block["task_id"], block_id, now),
                    ).fetchall()
                    future_ids = [row["id"] for row in future_rows]
                    if future_ids:
                        placeholders = ", ".join("?" for _ in future_ids)
                        conn.execute(
                            f"UPDATE planner_plan_block_calendar_links "
                            f"SET status = 'stale', updated_at = ? "
                            f"WHERE block_id IN ({placeholders}) "
                            "AND status = 'active'",
                            (now, *future_ids),
                        )
                        conn.execute(
                            f"UPDATE planner_plan_blocks SET status = 'done', "
                            f"updated_at = ? WHERE id IN ({placeholders})",
                            (now, *future_ids),
                        )
            else:
                conn.execute(
                    "UPDATE planner_plan_block_calendar_links "
                    "SET status = 'stale', updated_at = ? "
                    "WHERE block_id = ? AND status = 'active'",
                    (now, block_id),
                )

            conn.execute(
                "UPDATE planner_plan_blocks SET status = ?, updated_at = ? "
                "WHERE id = ?",
                (next_block_status, now, block_id),
            )
            conn.execute(
                "INSERT INTO planner_block_checkins "
                "(id, block_id, task_id, outcome, actual_minutes, "
                "credited_minutes, note, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _new_id("K"),
                    block_id,
                    block["task_id"],
                    outcome,
                    actual_minutes,
                    credited,
                    str(note)[:1000] if note else None,
                    now,
                ),
            )
        return self.get_plan_block(block_id)

    def list_plan_block_calendar_links(
        self, *, plan_id: str
    ) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT l.*, b.plan_id, b.task_id, b.starts_at, b.ends_at, "
                "b.status AS block_status, t.title "
                "FROM planner_plan_block_calendar_links l "
                "JOIN planner_plan_blocks b ON b.id = l.block_id "
                "JOIN planner_tasks t ON t.id = b.task_id "
                "WHERE b.plan_id = ? ORDER BY b.starts_at, l.calendar_id",
                (plan_id.upper(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_plan_calendar_preview(
        self,
        *,
        plan_id: str,
        provider: str,
        calendar_id: str,
        snapshot_hash: str,
        event_count: int,
        now: str,
    ) -> dict[str, Any]:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO planner_plan_calendar_previews "
                "(plan_id, provider, calendar_id, snapshot_hash, event_count, "
                "previewed_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(plan_id, provider, calendar_id) DO UPDATE SET "
                "snapshot_hash = excluded.snapshot_hash, "
                "event_count = excluded.event_count, "
                "previewed_at = excluded.previewed_at",
                (
                    plan_id.upper(),
                    provider,
                    calendar_id,
                    snapshot_hash,
                    int(event_count),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_plan_calendar_previews "
                "WHERE plan_id = ? AND provider = ? AND calendar_id = ?",
                (plan_id.upper(), provider, calendar_id),
            ).fetchone()
        return dict(row)

    def get_plan_calendar_preview(
        self,
        *,
        plan_id: str,
        provider: str,
        calendar_id: str,
    ) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_plan_calendar_previews "
                "WHERE plan_id = ? AND provider = ? AND calendar_id = ?",
                (plan_id.upper(), provider, calendar_id),
            ).fetchone()
        return self._dict(row)

    def delete_plan_calendar_preview(
        self,
        *,
        plan_id: str,
        provider: str,
        calendar_id: str,
    ) -> None:
        with self.write() as conn:
            conn.execute(
                "DELETE FROM planner_plan_calendar_previews "
                "WHERE plan_id = ? AND provider = ? AND calendar_id = ?",
                (plan_id.upper(), provider, calendar_id),
            )

    def create_ingest_source(
        self,
        *,
        provider: str,
        name: str,
        query: str,
        importance: str,
        max_results: int,
        now: str,
    ) -> dict[str, Any]:
        source_id = _new_id("F")
        try:
            with self.write() as conn:
                existing = conn.execute(
                    "SELECT id FROM planner_ingest_sources "
                    "WHERE provider = ? AND name = ? COLLATE NOCASE",
                    (provider, name),
                ).fetchone()
                if existing is not None:
                    raise ValueError(
                        f"ingest source name already exists: {name}"
                    )
                conn.execute(
                    "INSERT INTO planner_ingest_sources "
                    "(id, provider, name, query, importance, max_results, enabled, "
                    "last_scan_at, last_success_at, last_error, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, NULL, NULL, NULL, ?, ?)",
                    (
                        source_id,
                        provider,
                        name,
                        query,
                        importance,
                        int(max_results),
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM planner_ingest_sources WHERE id = ?",
                    (source_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"ingest source name already exists: {name}"
            ) from exc
        return dict(row)

    def get_ingest_source(
        self, source_id: str
    ) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_ingest_sources WHERE id = ?",
                (source_id.upper(),),
            ).fetchone()
        return self._dict(row)

    def list_ingest_sources(
        self,
        *,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        where = ""
        params: tuple[Any, ...] = ()
        if enabled is not None:
            where = "WHERE enabled = ?"
            params = (1 if enabled else 0,)
        with self.read() as conn:
            rows = conn.execute(
                "SELECT s.*, COUNT(r.id) AS receipt_count "
                "FROM planner_ingest_sources s "
                "LEFT JOIN planner_ingest_receipts r ON r.source_id = s.id "
                f"{where} GROUP BY s.id ORDER BY s.created_at, s.id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def set_ingest_source_enabled(
        self,
        *,
        source_id: str,
        enabled: bool,
        now: str,
    ) -> dict[str, Any] | None:
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_ingest_sources SET enabled = ?, updated_at = ? "
                "WHERE id = ?",
                (1 if enabled else 0, now, source_id.upper()),
            )
            row = conn.execute(
                "SELECT * FROM planner_ingest_sources WHERE id = ?",
                (source_id.upper(),),
            ).fetchone()
        return self._dict(row)

    def has_ingest_receipt(
        self,
        *,
        source_id: str,
        provider_item_id: str,
    ) -> bool:
        with self.read() as conn:
            row = conn.execute(
                "SELECT 1 FROM planner_ingest_receipts "
                "WHERE source_id = ? AND provider_item_id = ?",
                (source_id.upper(), provider_item_id),
            ).fetchone()
        return row is not None

    def record_ingest_receipt(
        self,
        *,
        source_id: str,
        provider_item_id: str,
        provider_thread_id: str | None,
        item_id: str,
        capture_outcome: str,
        subject: str | None,
        sender: str | None,
        received_at: str | None,
        scanned_at: str,
    ) -> bool:
        with self.write() as conn:
            inserted = conn.execute(
                "INSERT INTO planner_ingest_receipts "
                "(id, source_id, provider_item_id, provider_thread_id, item_id, "
                "capture_outcome, subject, sender, received_at, scanned_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id, provider_item_id) DO NOTHING",
                (
                    _new_id("R"),
                    source_id.upper(),
                    provider_item_id,
                    provider_thread_id,
                    item_id.upper(),
                    capture_outcome,
                    str(subject)[:500] if subject else None,
                    str(sender)[:500] if sender else None,
                    received_at,
                    scanned_at,
                ),
            )
        return bool(inserted.rowcount)

    def list_ingest_receipts(
        self,
        *,
        source_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.read() as conn:
            rows = conn.execute(
                "SELECT r.*, i.title, i.status AS item_status "
                "FROM planner_ingest_receipts r "
                "LEFT JOIN planner_items i ON i.id = r.item_id "
                "WHERE r.source_id = ? "
                "ORDER BY r.scanned_at DESC, r.id DESC LIMIT ?",
                (source_id.upper(), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_ingest_source_scan(
        self,
        *,
        source_id: str,
        scanned_at: str,
        error: str | None,
    ) -> None:
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_ingest_sources "
                "SET last_scan_at = ?, last_success_at = CASE WHEN ? IS NULL "
                "THEN ? ELSE last_success_at END, last_error = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    scanned_at,
                    error,
                    scanned_at,
                    str(error)[:1000] if error else None,
                    scanned_at,
                    source_id.upper(),
                ),
            )

    def create_profile_fact(
        self,
        *,
        category: str,
        fact_key: str,
        fact_value: str,
        normalized_value: str,
        privacy: str,
        source_type: str,
        source_uri: str | None,
        confidence: float,
        valid_until: str | None,
        now: str,
    ) -> dict[str, Any]:
        with self.write() as conn:
            existing = conn.execute(
                "SELECT * FROM planner_profile_facts "
                "WHERE category = ? AND fact_key = ? "
                "AND normalized_value = ? "
                "AND status IN ('proposed', 'confirmed') "
                "ORDER BY created_at DESC LIMIT 1",
                (category, fact_key, normalized_value),
            ).fetchone()
            if existing is not None:
                result = dict(existing)
                result["fact_outcome"] = "duplicate"
                return result
            fact_id = _new_id("PF")
            conn.execute(
                "INSERT INTO planner_profile_facts "
                "(id, category, fact_key, fact_value, normalized_value, status, "
                "privacy, source_type, source_uri, confidence, valid_until, "
                "created_at, updated_at, confirmed_at) "
                "VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    fact_id,
                    category,
                    fact_key,
                    fact_value,
                    normalized_value,
                    privacy,
                    source_type,
                    source_uri,
                    float(confidence),
                    valid_until,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_profile_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
        result = dict(row)
        result["fact_outcome"] = "created"
        return result

    def get_profile_fact(
        self, fact_id: str
    ) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_profile_facts WHERE id = ?",
                (fact_id.upper(),),
            ).fetchone()
        return self._dict(row)

    def list_profile_facts(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ""
        params: tuple[Any, ...] = ()
        if status:
            where = "WHERE status = ?"
            params = (status,)
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM planner_profile_facts "
                f"{where} ORDER BY category, fact_key, created_at",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def confirm_profile_fact(
        self,
        *,
        fact_id: str,
        now: str,
    ) -> dict[str, Any] | None:
        fact_id = fact_id.upper()
        with self.write() as conn:
            current = conn.execute(
                "SELECT * FROM planner_profile_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
            if current is None:
                return None
            if current["status"] == "confirmed":
                return dict(current)
            if current["status"] != "proposed":
                raise ValueError(
                    f"profile fact {fact_id} cannot be confirmed from "
                    f"status {current['status']}"
                )
            conn.execute(
                "UPDATE planner_profile_facts "
                "SET status = 'superseded', updated_at = ? "
                "WHERE category = ? AND fact_key = ? "
                "AND status = 'confirmed' AND id != ?",
                (
                    now,
                    current["category"],
                    current["fact_key"],
                    fact_id,
                ),
            )
            conn.execute(
                "UPDATE planner_profile_facts "
                "SET status = 'confirmed', confirmed_at = ?, updated_at = ? "
                "WHERE id = ?",
                (now, now, fact_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_profile_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
        return dict(row)

    def reject_profile_fact(
        self,
        *,
        fact_id: str,
        now: str,
    ) -> dict[str, Any] | None:
        fact_id = fact_id.upper()
        with self.write() as conn:
            current = conn.execute(
                "SELECT * FROM planner_profile_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
            if current is None:
                return None
            if current["status"] == "rejected":
                return dict(current)
            if current["status"] != "proposed":
                raise ValueError(
                    f"profile fact {fact_id} cannot be rejected from "
                    f"status {current['status']}"
                )
            conn.execute(
                "UPDATE planner_profile_facts "
                "SET status = 'rejected', updated_at = ? WHERE id = ?",
                (now, fact_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_profile_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
        return dict(row)

    def revoke_profile_fact(
        self,
        *,
        fact_id: str,
        now: str,
    ) -> dict[str, Any] | None:
        fact_id = fact_id.upper()
        with self.write() as conn:
            current = conn.execute(
                "SELECT * FROM planner_profile_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
            if current is None:
                return None
            if current["status"] == "revoked":
                return dict(current)
            if current["status"] != "confirmed":
                raise ValueError(
                    f"profile fact {fact_id} cannot be revoked from "
                    f"status {current['status']}"
                )
            conn.execute(
                "UPDATE planner_profile_facts "
                "SET status = 'revoked', updated_at = ? WHERE id = ?",
                (now, fact_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_profile_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
        return dict(row)

    def sync_monitor_proposals(
        self,
        *,
        proposals: Iterable[dict[str, Any]],
        now: str,
    ) -> list[dict[str, Any]]:
        values = list(proposals)
        current_keys = [value["proposal_key"] for value in values]
        with self.write() as conn:
            if current_keys:
                placeholders = ", ".join("?" for _ in current_keys)
                conn.execute(
                    "UPDATE planner_monitor_proposals "
                    "SET status = 'superseded', updated_at = ? "
                    "WHERE status IN ('proposed', 'approved') "
                    f"AND proposal_key NOT IN ({placeholders})",
                    (now, *current_keys),
                )
            else:
                conn.execute(
                    "UPDATE planner_monitor_proposals "
                    "SET status = 'superseded', updated_at = ? "
                    "WHERE status IN ('proposed', 'approved')",
                    (now,),
                )
            for proposal in values:
                existing = conn.execute(
                    "SELECT * FROM planner_monitor_proposals "
                    "WHERE proposal_key = ?",
                    (proposal["proposal_key"],),
                ).fetchone()
                encoded = (
                    json.dumps(
                        proposal["source_kinds"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        proposal["query_terms"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        proposal["evidence"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                if existing is None:
                    conn.execute(
                        "INSERT INTO planner_monitor_proposals "
                        "(id, proposal_key, topic_key, category, title, "
                        "rationale, priority, cadence, source_kinds_json, "
                        "query_terms_json, evidence_json, status, created_at, "
                        "updated_at, decided_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', "
                        "?, ?, NULL)",
                        (
                            _new_id("MP"),
                            proposal["proposal_key"],
                            proposal["topic_key"],
                            proposal["category"],
                            proposal["title"],
                            proposal["rationale"],
                            proposal["priority"],
                            proposal["cadence"],
                            *encoded,
                            now,
                            now,
                        ),
                    )
                else:
                    next_status = (
                        "proposed"
                        if existing["status"] == "superseded"
                        else existing["status"]
                    )
                    conn.execute(
                        "UPDATE planner_monitor_proposals "
                        "SET topic_key = ?, category = ?, title = ?, "
                        "rationale = ?, priority = ?, cadence = ?, "
                        "source_kinds_json = ?, query_terms_json = ?, "
                        "evidence_json = ?, status = ?, updated_at = ?, "
                        "decided_at = CASE WHEN ? = 'proposed' THEN NULL "
                        "ELSE decided_at END WHERE proposal_key = ?",
                        (
                            proposal["topic_key"],
                            proposal["category"],
                            proposal["title"],
                            proposal["rationale"],
                            proposal["priority"],
                            proposal["cadence"],
                            *encoded,
                            next_status,
                            now,
                            next_status,
                            proposal["proposal_key"],
                        ),
                    )
        return self.list_monitor_proposals(status=None)

    def get_monitor_proposal(
        self, proposal_id: str
    ) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_monitor_proposals WHERE id = ?",
                (proposal_id.upper(),),
            ).fetchone()
        return self._monitor_proposal_dict(row)

    def list_monitor_proposals(
        self,
        *,
        status: str | None,
    ) -> list[dict[str, Any]]:
        where = ""
        params: tuple[Any, ...] = ()
        if status:
            where = "WHERE status = ?"
            params = (status,)
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM planner_monitor_proposals "
                f"{where} ORDER BY "
                "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'normal' THEN 2 ELSE 3 END, category, title",
                params,
            ).fetchall()
        return [self._monitor_proposal_dict(row) for row in rows]

    def decide_monitor_proposal(
        self,
        *,
        proposal_id: str,
        decision: str,
        now: str,
    ) -> dict[str, Any] | None:
        proposal_id = proposal_id.upper()
        with self.write() as conn:
            current = conn.execute(
                "SELECT * FROM planner_monitor_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if current is None:
                return None
            if current["status"] == decision:
                return self._monitor_proposal_dict(current)
            if current["status"] not in {"proposed", "approved", "rejected"}:
                raise ValueError(
                    f"monitor proposal {proposal_id} cannot be changed from "
                    f"status {current['status']}"
                )
            conn.execute(
                "UPDATE planner_monitor_proposals "
                "SET status = ?, decided_at = ?, updated_at = ? WHERE id = ?",
                (decision, now, now, proposal_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_monitor_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._monitor_proposal_dict(row)

    @staticmethod
    def _monitor_proposal_dict(
        row: sqlite3.Row | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["source_kinds"] = json.loads(
            result.pop("source_kinds_json")
        )
        result["query_terms"] = json.loads(
            result.pop("query_terms_json")
        )
        result["evidence"] = json.loads(result.pop("evidence_json"))
        return result

    def reserve_plan_block_calendar_link(
        self,
        *,
        block_id: str,
        provider: str,
        calendar_id: str,
        payload_hash: str,
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        with self.write() as conn:
            state = conn.execute(
                "SELECT b.status AS block_status, r.status AS plan_status "
                "FROM planner_plan_blocks b "
                "JOIN planner_plan_runs r ON r.id = b.plan_id "
                "WHERE b.id = ?",
                (block_id.upper(),),
            ).fetchone()
            if state is None:
                raise ValueError(f"planner block not found: {block_id}")
            if (
                state["plan_status"] != "confirmed"
                or state["block_status"] != "confirmed"
            ):
                raise ValueError(
                    f"block {block_id.upper()} changed before calendar write "
                    f"({state['plan_status']}/{state['block_status']})"
                )
            inserted = conn.execute(
                "INSERT INTO planner_plan_block_calendar_links "
                "(id, block_id, provider, calendar_id, external_event_id, "
                "html_link, payload_hash, status, last_error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, 'creating', NULL, ?, ?) "
                "ON CONFLICT(block_id, provider, calendar_id) DO NOTHING",
                (
                    _new_id("X"),
                    block_id.upper(),
                    provider,
                    calendar_id,
                    payload_hash,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM planner_plan_block_calendar_links "
                "WHERE block_id = ? AND provider = ? AND calendar_id = ?",
                (block_id.upper(), provider, calendar_id),
            ).fetchone()
        return dict(row), bool(inserted.rowcount)

    def complete_plan_block_calendar_link(
        self,
        *,
        link_id: str,
        external_event_id: str,
        html_link: str | None,
        now: str,
    ) -> dict[str, Any]:
        with self.write() as conn:
            state = conn.execute(
                "SELECT b.status AS block_status, r.status AS plan_status "
                "FROM planner_plan_block_calendar_links l "
                "JOIN planner_plan_blocks b ON b.id = l.block_id "
                "JOIN planner_plan_runs r ON r.id = b.plan_id "
                "WHERE l.id = ?",
                (link_id,),
            ).fetchone()
            if state is None:
                raise RuntimeError(f"block calendar link {link_id} was not found")
            next_status = (
                "active"
                if state["plan_status"] == "confirmed"
                and state["block_status"] == "confirmed"
                else "stale"
            )
            changed = conn.execute(
                "UPDATE planner_plan_block_calendar_links "
                "SET external_event_id = ?, html_link = ?, status = ?, "
                "last_error = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'creating'",
                (external_event_id, html_link, next_status, now, link_id),
            ).rowcount
            if not changed:
                raise RuntimeError(
                    f"block calendar link {link_id} is no longer reserved"
                )
            row = conn.execute(
                "SELECT * FROM planner_plan_block_calendar_links WHERE id = ?",
                (link_id,),
            ).fetchone()
        return dict(row)

    def fail_plan_block_calendar_link(
        self, *, link_id: str, error: str, now: str
    ) -> None:
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_plan_block_calendar_links "
                "SET status = 'failed', last_error = ?, updated_at = ? "
                "WHERE id = ? AND status = 'creating'",
                (str(error)[:1000], now, link_id),
            )

    def delete_plan_block_calendar_links(
        self,
        *,
        plan_id: str,
        provider: str,
        calendar_id: str,
    ) -> list[dict[str, Any]]:
        with self.write() as conn:
            rows = conn.execute(
                "SELECT l.* FROM planner_plan_block_calendar_links l "
                "JOIN planner_plan_blocks b ON b.id = l.block_id "
                "WHERE b.plan_id = ? AND l.provider = ? AND l.calendar_id = ?",
                (plan_id.upper(), provider, calendar_id),
            ).fetchall()
            conn.execute(
                "DELETE FROM planner_plan_block_calendar_links "
                "WHERE provider = ? AND calendar_id = ? "
                "AND block_id IN (SELECT id FROM planner_plan_blocks "
                "WHERE plan_id = ?)",
                (provider, calendar_id, plan_id.upper()),
            )
        return [dict(row) for row in rows]

    def overlapping_confirmed_plan_ids(
        self, *, window_start: str, window_end: str
    ) -> list[str]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT id FROM planner_plan_runs WHERE status = 'confirmed' "
                "AND window_end > ? AND window_start < ? ORDER BY created_at",
                (window_start, window_end),
            ).fetchall()
        return [row["id"] for row in rows]

    def create_plan_draft(
        self,
        *,
        window_start: str,
        window_end: str,
        timezone_name: str,
        is_replan: bool,
        replaces_plan_ids: Iterable[str],
        summary: dict[str, Any],
        blocks: Iterable[dict[str, Any]],
        now: str,
    ) -> dict[str, Any]:
        plan_id = _new_id("S")
        replacement_ids = list(dict.fromkeys(str(value) for value in replaces_plan_ids))
        with self.write() as conn:
            old_drafts = conn.execute(
                "SELECT id FROM planner_plan_runs WHERE status = 'draft' "
                "AND window_end > ? AND window_start < ?",
                (window_start, window_end),
            ).fetchall()
            if old_drafts:
                old_ids = [row["id"] for row in old_drafts]
                placeholders = ", ".join("?" for _ in old_ids)
                conn.execute(
                    f"UPDATE planner_plan_blocks SET status = 'superseded', updated_at = ? "
                    f"WHERE plan_id IN ({placeholders}) AND status = 'proposed'",
                    (now, *old_ids),
                )
                conn.execute(
                    f"UPDATE planner_plan_runs SET status = 'superseded', updated_at = ? "
                    f"WHERE id IN ({placeholders})",
                    (now, *old_ids),
                )
            conn.execute(
                "INSERT INTO planner_plan_runs "
                "(id, window_start, window_end, timezone, status, is_replan, "
                "replaces_plan_ids, summary_json, created_at, updated_at, confirmed_at) "
                "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, NULL)",
                (
                    plan_id,
                    window_start,
                    window_end,
                    timezone_name,
                    1 if is_replan else 0,
                    json.dumps(replacement_ids),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            for block in blocks:
                conn.execute(
                    "INSERT INTO planner_plan_blocks "
                    "(id, plan_id, task_id, starts_at, ends_at, minutes, energy, "
                    "availability_label, priority_score, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)",
                    (
                        _new_id("B"),
                        plan_id,
                        block["task_id"],
                        block["starts_at"],
                        block["ends_at"],
                        block["minutes"],
                        block["energy"],
                        block.get("availability_label"),
                        block["priority_score"],
                        now,
                        now,
                    ),
                )
        result = self.get_plan(plan_id)
        assert result is not None
        return result

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM planner_plan_runs WHERE id = ?", (plan_id.upper(),)
            ).fetchone()
            if row is None:
                return None
            blocks = conn.execute(
                "SELECT b.*, t.title, t.goal_id FROM planner_plan_blocks b "
                "JOIN planner_tasks t ON t.id = b.task_id "
                "WHERE b.plan_id = ? ORDER BY b.starts_at, b.id",
                (plan_id.upper(),),
            ).fetchall()
            checkins = conn.execute(
                "SELECT c.* FROM planner_block_checkins c "
                "JOIN planner_plan_blocks b ON b.id = c.block_id "
                "WHERE b.plan_id = ?",
                (plan_id.upper(),),
            ).fetchall()
            links = conn.execute(
                "SELECT l.* FROM planner_plan_block_calendar_links l "
                "JOIN planner_plan_blocks b ON b.id = l.block_id "
                "WHERE b.plan_id = ? ORDER BY l.calendar_id, l.created_at",
                (plan_id.upper(),),
            ).fetchall()
        result = dict(row)
        result["replaces_plan_ids"] = json.loads(result["replaces_plan_ids"])
        result["summary"] = json.loads(result.pop("summary_json"))
        checkin_map = {value["block_id"]: dict(value) for value in checkins}
        link_map: dict[str, list[dict[str, Any]]] = {}
        for link in links:
            link_map.setdefault(link["block_id"], []).append(dict(link))
        result["blocks"] = [
            {
                **dict(block),
                "checkin": checkin_map.get(block["id"]),
                "calendar_links": link_map.get(block["id"], []),
            }
            for block in blocks
        ]
        return result

    def list_plans(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.read() as conn:
            rows = conn.execute(
                "SELECT r.*, COUNT(b.id) AS block_count, "
                "COALESCE(SUM(b.minutes), 0) AS planned_minutes "
                "FROM planner_plan_runs r "
                "LEFT JOIN planner_plan_blocks b ON b.plan_id = r.id "
                "GROUP BY r.id ORDER BY r.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["replaces_plan_ids"] = json.loads(value["replaces_plan_ids"])
            value["summary"] = json.loads(value.pop("summary_json"))
            result.append(value)
        return result

    def confirm_plan(
        self, plan_id: str, *, replace: bool, now: str
    ) -> dict[str, Any] | None:
        plan_id = plan_id.upper()
        with self.write() as conn:
            run = conn.execute(
                "SELECT * FROM planner_plan_runs WHERE id = ?", (plan_id,)
            ).fetchone()
            if run is None:
                return None
            if run["status"] == "confirmed":
                pass
            elif run["status"] != "draft":
                raise ValueError(
                    f"plan {plan_id} cannot be confirmed from status {run['status']}"
                )
            else:
                replacements = json.loads(run["replaces_plan_ids"])
                if replacements and not replace:
                    raise ValueError(
                        "this is a replan; confirmation requires replace=True"
                    )
                stale_task = conn.execute(
                    "SELECT t.id FROM planner_plan_blocks b "
                    "JOIN planner_tasks t ON t.id = b.task_id "
                    "LEFT JOIN planner_goals g ON g.id = t.goal_id "
                    "WHERE b.plan_id = ? AND b.status = 'proposed' "
                    "AND (t.status NOT IN ('backlog', 'planned') "
                    "OR (t.goal_id IS NOT NULL AND g.status != 'active')) "
                    "LIMIT 1",
                    (plan_id,),
                ).fetchone()
                if stale_task is not None:
                    raise ValueError(
                        f"task {stale_task['id']} changed after preview; "
                        "generate a fresh schedule"
                    )
                excluded = tuple(replacements)
                exclusion = ""
                params: list[Any] = [plan_id]
                if excluded:
                    exclusion = (
                        f"AND existing.plan_id NOT IN "
                        f"({', '.join('?' for _ in excluded)}) "
                    )
                    params.extend(excluded)
                conflicts = conn.execute(
                    "SELECT DISTINCT existing.plan_id "
                    "FROM planner_plan_blocks existing "
                    "JOIN planner_plan_runs existing_run "
                    "ON existing_run.id = existing.plan_id "
                    "JOIN planner_plan_blocks proposed "
                    "ON proposed.plan_id = ? "
                    "AND existing.ends_at > proposed.starts_at "
                    "AND existing.starts_at < proposed.ends_at "
                    "WHERE existing_run.status = 'confirmed' "
                    "AND existing.status = 'confirmed' "
                    f"{exclusion}"
                    "LIMIT 1",
                    tuple(params),
                ).fetchone()
                if conflicts is not None:
                    raise ValueError(
                        f"plan conflicts with confirmed plan {conflicts['plan_id']}; "
                        "generate a fresh preview"
                    )
                if replacements:
                    placeholders = ", ".join("?" for _ in replacements)
                    conn.execute(
                        f"UPDATE planner_plan_block_calendar_links "
                        f"SET status = 'stale', updated_at = ? "
                        f"WHERE block_id IN (SELECT id FROM planner_plan_blocks "
                        f"WHERE plan_id IN ({placeholders})) "
                        "AND status = 'active'",
                        (now, *replacements),
                    )
                    conn.execute(
                        f"UPDATE planner_plan_blocks SET status = 'superseded', "
                        f"updated_at = ? WHERE plan_id IN ({placeholders}) "
                        "AND status = 'confirmed'",
                        (now, *replacements),
                    )
                    conn.execute(
                        f"UPDATE planner_plan_runs SET status = 'superseded', "
                        f"updated_at = ? WHERE id IN ({placeholders}) "
                        "AND status = 'confirmed'",
                        (now, *replacements),
                    )
                conn.execute(
                    "UPDATE planner_plan_runs SET status = 'confirmed', "
                    "confirmed_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_plan_blocks SET status = 'confirmed', "
                    "updated_at = ? WHERE plan_id = ? AND status = 'proposed'",
                    (now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_tasks SET status = 'planned', updated_at = ? "
                    "WHERE id IN (SELECT task_id FROM planner_plan_blocks "
                    "WHERE plan_id = ?) AND status = 'backlog'",
                    (now, plan_id),
                )
        return self.get_plan(plan_id)

    def cancel_plan(self, plan_id: str, *, now: str) -> dict[str, Any] | None:
        plan_id = plan_id.upper()
        with self.write() as conn:
            run = conn.execute(
                "SELECT * FROM planner_plan_runs WHERE id = ?", (plan_id,)
            ).fetchone()
            if run is None:
                return None
            if run["status"] == "superseded":
                raise ValueError(f"superseded plan {plan_id} cannot be cancelled")
            if run["status"] != "cancelled":
                task_rows = conn.execute(
                    "SELECT DISTINCT task_id FROM planner_plan_blocks WHERE plan_id = ?",
                    (plan_id,),
                ).fetchall()
                task_ids = [row["task_id"] for row in task_rows]
                conn.execute(
                    "UPDATE planner_plan_block_calendar_links SET status = 'stale', "
                    "updated_at = ? WHERE block_id IN "
                    "(SELECT id FROM planner_plan_blocks WHERE plan_id = ?) "
                    "AND status = 'active'",
                    (now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_plan_blocks SET status = 'skipped', updated_at = ? "
                    "WHERE plan_id = ? AND status IN ('proposed', 'confirmed')",
                    (now, plan_id),
                )
                conn.execute(
                    "UPDATE planner_plan_runs SET status = 'cancelled', updated_at = ? "
                    "WHERE id = ?",
                    (now, plan_id),
                )
                for task_id in task_ids:
                    has_active_block = conn.execute(
                        "SELECT 1 FROM planner_plan_blocks b "
                        "JOIN planner_plan_runs r ON r.id = b.plan_id "
                        "WHERE b.task_id = ? AND r.status = 'confirmed' "
                        "AND b.status = 'confirmed' LIMIT 1",
                        (task_id,),
                    ).fetchone()
                    if has_active_block is None:
                        conn.execute(
                            "UPDATE planner_tasks SET status = 'backlog', updated_at = ? "
                            "WHERE id = ? AND status = 'planned'",
                            (now, task_id),
                        )
        return self.get_plan(plan_id)

    def acknowledge_item(self, item_id: str, *, now: str) -> dict[str, Any] | None:
        item_id = item_id.upper()
        with self.write() as conn:
            changed = conn.execute(
                "UPDATE planner_items SET acknowledged_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'confirmed'",
                (now, now, item_id),
            ).rowcount
            if not changed:
                return self._dict(
                    conn.execute(
                        "SELECT * FROM planner_items WHERE id = ?", (item_id,)
                    ).fetchone()
                )
            conn.execute(
                "UPDATE planner_nudges SET status = 'acknowledged', acknowledged_at = ? "
                "WHERE item_id = ? AND status = 'sent'",
                (now, item_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (item_id,)
            ).fetchone()
        return dict(row)

    def snooze_item(
        self, item_id: str, *, until: str, now: str
    ) -> dict[str, Any] | None:
        item_id = item_id.upper()
        with self.write() as conn:
            changed = conn.execute(
                "UPDATE planner_items SET snoozed_until = ?, updated_at = ? "
                "WHERE id = ? AND status = 'confirmed'",
                (until, now, item_id),
            ).rowcount
            if not changed:
                return self._dict(
                    conn.execute(
                        "SELECT * FROM planner_items WHERE id = ?", (item_id,)
                    ).fetchone()
                )
            conn.execute(
                "UPDATE planner_nudges SET status = 'cancelled' "
                "WHERE item_id = ? AND status = 'pending' AND fire_at <= ?",
                (item_id, until),
            )
            conn.execute(
                "INSERT OR IGNORE INTO planner_nudges "
                "(id, item_id, kind, fire_at, severity, ack_required, status, "
                "idempotency_key, created_at) VALUES (?, ?, 'snooze', ?, 'normal', 1, "
                "'pending', ?, ?)",
                (_new_id("N"), item_id, until, f"{item_id}:snooze:{until}", now),
            )
            row = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (item_id,)
            ).fetchone()
        return dict(row)

    def complete_item(self, item_id: str, *, now: str) -> dict[str, Any] | None:
        item_id = item_id.upper()
        with self.write() as conn:
            conn.execute(
                "UPDATE planner_items SET status = 'done', completed_at = ?, "
                "updated_at = ?, snoozed_until = NULL WHERE id = ? "
                "AND status IN ('draft', 'confirmed')",
                (now, now, item_id),
            )
            conn.execute(
                "UPDATE planner_nudges SET status = 'cancelled' "
                "WHERE item_id = ? AND status = 'pending'",
                (item_id,),
            )
            row = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._dict(row)

    def cancel_item(self, item_id: str, *, now: str) -> dict[str, Any] | None:
        item_id = item_id.upper()
        with self.write() as conn:
            current = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (item_id,)
            ).fetchone()
            if current is None:
                return None
            if current["status"] == "cancelled":
                return dict(current)
            if current["status"] == "done":
                raise ValueError(f"completed item {item_id} cannot be cancelled")
            conn.execute(
                "UPDATE planner_items SET status = 'cancelled', updated_at = ?, "
                "snoozed_until = NULL WHERE id = ? AND status IN ('draft', 'confirmed')",
                (now, item_id),
            )
            conn.execute(
                "UPDATE planner_nudges SET status = 'cancelled' "
                "WHERE item_id = ? AND status = 'pending'",
                (item_id,),
            )
            conn.execute(
                "UPDATE planner_calendar_links SET status = 'stale', updated_at = ? "
                "WHERE item_id = ? AND status = 'active'",
                (now, item_id),
            )
            row = conn.execute(
                "SELECT * FROM planner_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._dict(row)

    def claim_due_nudges(self, *, now: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.write() as conn:
            rows = conn.execute(
                "SELECT n.*, i.title, i.raw_content, i.timezone, i.actionable_at, "
                "i.due_at, i.event_at, i.importance "
                "FROM planner_nudges n JOIN planner_items i ON i.id = n.item_id "
                "WHERE n.status = 'pending' AND n.fire_at <= ? "
                "AND i.status = 'confirmed' "
                "AND (i.snoozed_until IS NULL OR i.snoozed_until <= ?) "
                "ORDER BY n.fire_at, n.id LIMIT ?",
                (now, now, limit),
            ).fetchall()
            if rows:
                conn.executemany(
                    "UPDATE planner_nudges SET status = 'sent', sent_at = ? "
                    "WHERE id = ? AND status = 'pending'",
                    [(now, row["id"]) for row in rows],
                )
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.read() as conn:
            item_rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM planner_items GROUP BY status"
            ).fetchall()
            due = conn.execute(
                "SELECT COUNT(*) FROM planner_nudges WHERE status = 'pending'"
            ).fetchone()[0]
            calendar_links = conn.execute(
                "SELECT COUNT(*) FROM planner_calendar_links WHERE status = 'active'"
            ).fetchone()[0]
            active_goals = conn.execute(
                "SELECT COUNT(*) FROM planner_goals WHERE status = 'active'"
            ).fetchone()[0]
            open_tasks = conn.execute(
                "SELECT COUNT(*) FROM planner_tasks "
                "WHERE status IN ('backlog', 'planned')"
            ).fetchone()[0]
            confirmed_plans = conn.execute(
                "SELECT COUNT(*) FROM planner_plan_runs WHERE status = 'confirmed'"
            ).fetchone()[0]
            enabled_ingest_sources = conn.execute(
                "SELECT COUNT(*) FROM planner_ingest_sources WHERE enabled = 1"
            ).fetchone()[0]
            ingest_receipts = conn.execute(
                "SELECT COUNT(*) FROM planner_ingest_receipts"
            ).fetchone()[0]
            confirmed_profile_facts = conn.execute(
                "SELECT COUNT(*) FROM planner_profile_facts "
                "WHERE status = 'confirmed'"
            ).fetchone()[0]
            proposed_monitors = conn.execute(
                "SELECT COUNT(*) FROM planner_monitor_proposals "
                "WHERE status = 'proposed'"
            ).fetchone()[0]
        counts = {"draft": 0, "confirmed": 0, "done": 0, "cancelled": 0}
        counts.update({row["status"]: int(row["count"]) for row in item_rows})
        counts["pending_nudges"] = int(due)
        counts["calendar_links"] = int(calendar_links)
        counts["active_goals"] = int(active_goals)
        counts["open_tasks"] = int(open_tasks)
        counts["confirmed_plans"] = int(confirmed_plans)
        counts["enabled_ingest_sources"] = int(enabled_ingest_sources)
        counts["ingest_receipts"] = int(ingest_receipts)
        counts["confirmed_profile_facts"] = int(
            confirmed_profile_facts
        )
        counts["proposed_monitors"] = int(proposed_monitors)
        return counts
