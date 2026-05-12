"""
FSH Command Center — Manus Adapter
====================================
Routes tasks to Manus AI for browser automation and web research.
Pillars: commerce, content, gridline (research leg)

Manus handles tasks requiring:
  - Web browsing / scraping
  - Form submission
  - Multi-step browser automation
  - Research aggregation

Review-ref: fsh_architecture_review.md §CRITICAL GAPS — adapter specifications
"""
from __future__ import annotations

import os
from typing import Any

import requests  # type: ignore

from .base import (
    FSHAdapterBase,
    FSHTask,
    FSHTaskResult,
    ApprovalRequiredError,
    PillarIsolationError,
)

MANUS_PILLARS = {"commerce", "content", "gridline"}

# Browser automation tasks touching external systems require approval
EXTERNAL_TASK_PATTERNS = {"order_place", "cart_submit", "checkout", "publish_listing"}


class ManusAdapter(FSHAdapterBase):
    """
    Sends FSH tasks to Manus AI for browser automation.

    Configuration (from environment):
        MANUS_API_URL   — Manus API base URL
        MANUS_API_KEY   — Manus API key
        MANUS_TIMEOUT   — seconds (default 180)
    """

    adapter_name = "manus"

    def __init__(
        self,
        api_url:  str | None = None,
        api_key:  str | None = None,
        timeout:  int = 180,
    ):
        self.api_url = api_url or os.environ["MANUS_API_URL"]
        self.api_key = api_key or os.environ["MANUS_API_KEY"]
        self.timeout = timeout

    # ── Phase 1 ───────────────────────────────────────────────────────────────

    def translate_in(self, raw_payload: dict[str, Any]) -> FSHTask:
        task = FSHTask(
            schema_version   = raw_payload.get("schema_version", "1.0.1"),
            task_id          = raw_payload.get("task_id") or self.new_task_id(),
            task_type        = raw_payload["task_type"],
            pillar           = raw_payload["pillar"],
            objective        = raw_payload["objective"],
            approval_level   = int(raw_payload.get("approval_level", 0)),
            execution_engine = raw_payload.get("execution_engine", "manus"),
            priority         = int(raw_payload.get("priority", 2)),
            compliance_flags = raw_payload.get("compliance_flags", []),
            idempotency_key  = raw_payload.get("idempotency_key"),
            context_artifacts= raw_payload.get("context_artifacts", []),
            expires_at       = raw_payload.get("expires_at"),
            retry_policy     = raw_payload.get("retry_policy", {}),
            parent_task_id   = raw_payload.get("parent_task_id"),
        )
        task.validate()

        if task.pillar not in MANUS_PILLARS:
            raise PillarIsolationError(
                f"ManusAdapter handles {MANUS_PILLARS}, received pillar='{task.pillar}'"
            )
        return task

    # ── Phase 2 ───────────────────────────────────────────────────────────────

    def execute(self, task: FSHTask) -> FSHTaskResult:
        # Approval gate for external_action and specific automation patterns
        is_external = "external_action" in task.compliance_flags
        is_dangerous = any(p in task.task_type for p in EXTERNAL_TASK_PATTERNS)

        if is_external or is_dangerous:
            if task.approval_level < 1:
                raise ApprovalRequiredError(
                    task_id        = task.task_id,
                    approval_level = 1,
                    reason         = (
                        f"Manus task '{task.task_type}' touches external systems "
                        f"and requires approval_level >= 1."
                    ),
                )

        payload = {
            "session_id":     f"fsh-{task.pillar}-{task.task_id[:8]}",
            "task_id":        task.task_id,
            "task_type":      task.task_type,
            "instructions":   task.objective,
            "context":        task.context_artifacts,
            "headless":       True,
            "record_session": True,   # capture screenshots/video for audit
        }

        self.audit_log(task, "manus_session_start", {"pillar": task.pillar})

        try:
            response = requests.post(
                f"{self.api_url}/v1/sessions/run",
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "X-Task-Id":     task.task_id,
                },
                json    = payload,
                timeout = self.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            return FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = f"Manus session timed out after {self.timeout}s",
                error_detail   = {"error_class": "timeout"},
            )
        except requests.exceptions.HTTPError as exc:
            return FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = f"Manus API error: {exc.response.status_code if exc.response else 'unknown'}",
                error_detail   = {
                    "error_class": "http_error",
                    "status_code": exc.response.status_code if exc.response else None,
                },
            )

        data = response.json()
        self.audit_log(task, "manus_session_end",
                       {"status": data.get("status"), "steps": data.get("step_count")})

        artifacts = []
        if data.get("screenshots"):
            artifacts = [
                {"type": "image", "label": f"screenshot_{i}", "url": url}
                for i, url in enumerate(data["screenshots"])
            ]

        return FSHTaskResult(
            task_id        = task.task_id,
            success        = data.get("status") == "completed",
            result_summary = data.get("summary", ""),
            artifacts      = artifacts,
            error_detail   = data.get("error") if data.get("status") != "completed" else None,
        )

    # ── Phase 3 ───────────────────────────────────────────────────────────────

    def translate_out(self, result: FSHTaskResult) -> dict[str, Any]:
        return {
            "task_id":        result.task_id,
            "adapter":        self.adapter_name,
            "success":        result.success,
            "result_summary": result.result_summary,
            "artifacts":      result.artifacts,
            "error_detail":   result.error_detail,
            "completed_at":   result.completed_at,
        }
