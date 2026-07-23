"""Read-only Gmail adapter and message normalization for Planner ingestion."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .calendar_sync import google_workspace_script_path


class GmailIngestError(RuntimeError):
    pass


class GoogleWorkspaceGmail:
    """Thin read-only adapter over Hermes' existing Google Workspace helper."""

    provider = "gmail"

    def __init__(self, script_path: Path | str | None = None):
        self.script_path = (
            Path(script_path)
            if script_path is not None
            else google_workspace_script_path()
        )

    def search_messages(
        self,
        *,
        query: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        payload = self._run(
            "search",
            query,
            "--max",
            str(max(1, min(int(max_results), 100))),
        )
        if not isinstance(payload, list):
            raise GmailIngestError("Gmail search did not return a message list")
        return [value for value in payload if isinstance(value, dict)]

    def get_message(self, *, message_id: str) -> dict[str, Any]:
        payload = self._run("get", message_id)
        if not isinstance(payload, dict):
            raise GmailIngestError("Gmail get did not return a message object")
        if not str(payload.get("id") or "").strip():
            raise GmailIngestError("Gmail get did not return a message id")
        return payload

    def _run(self, *arguments: str) -> Any:
        if not self.script_path.is_file():
            raise GmailIngestError(
                f"Google Workspace Gmail helper not found: {self.script_path}"
            )
        completed = subprocess.run(
            [
                sys.executable,
                str(self.script_path),
                "gmail",
                *arguments,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise GmailIngestError(
                detail or f"Gmail helper exited with code {completed.returncode}"
            )
        output = completed.stdout.strip()
        if output == "No messages found.":
            return []
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise GmailIngestError(
                f"Gmail helper returned invalid JSON: {output[:300]}"
            ) from exc


def gmail_message_source_uri(message: dict[str, Any]) -> str:
    """Use the thread as identity so follow-up notices revise one commitment."""
    identity = str(
        message.get("threadId") or message.get("id") or ""
    ).strip()
    return f"gmail://thread/{quote(identity, safe='')}"


def gmail_message_content(
    message: dict[str, Any],
    *,
    maximum_chars: int = 30000,
) -> str:
    subject = str(message.get("subject") or "(no subject)").strip()
    sender = str(message.get("from") or "").strip()
    received = str(message.get("date") or "").strip()
    body = str(message.get("body") or message.get("snippet") or "").strip()
    lines = [f"Subject: {subject}"]
    if sender:
        lines.append(f"From: {sender}")
    if received:
        lines.append(f"Date: {received}")
    if body:
        lines.extend(("", body))
    return "\n".join(lines)[:maximum_chars].strip()


def gmail_message_datetime(
    message: dict[str, Any],
    *,
    fallback: datetime,
) -> datetime:
    raw = str(message.get("date") or "").strip()
    if not raw:
        return fallback
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def render_ingest_message(report: dict[str, Any]) -> str:
    """Render a compact Cron/gateway message only when attention is needed."""
    captured = report.get("captured") or []
    errors = report.get("errors") or []
    if not captured and not errors:
        return ""
    lines = ["Personal Planner inbox scan"]
    drafts = [
        value
        for value in captured
        if value.get("item_status") == "draft"
    ]
    if drafts:
        lines.append(f"New or updated drafts: {len(drafts)}")
        for value in drafts[:8]:
            lines.append(f"- [{value['item_id']}] {value['title']}")
        if len(drafts) > 8:
            lines.append(f"- … and {len(drafts) - 8} more")
        lines.append(
            "Review each draft, extract its dates, then confirm it explicitly."
        )
    if errors:
        lines.append(f"Source or message errors: {len(errors)}")
        for error in errors[:3]:
            lines.append(f"- {error}")
    return "\n".join(lines)
