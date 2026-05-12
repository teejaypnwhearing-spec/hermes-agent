"""
FSH Command Center — Abacus DeepAgent Adapter
=============================================
Routes tasks to Abacus AI DeepAgent for pillar automation.
Pillars: gridline, commerce, trading

⚠️  P0 FIX: Blocking approval poll REMOVED.
    The original spec had:
        while True:
            status = postgres.query_one(...)
            if status in ["approved", "rejected"]: break
            time.sleep(60)   # ← holds DB connection up to 48 hours
    This adapter instead raises ApprovalRequiredError immediately.
    The orchestrator creates an approval_request row and n8n fires
    a callback via pg_notify when the human decides.

Review-ref: fsh_architecture_review.md §P0 — BLOCKING APPROVAL POLL
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import requests  # type: ignore  # install: pip install requests

from .base import (
    FSHAdapterBase,
    FSHTask,
    FSHTaskResult,
    ApprovalRequiredError,
    IdempotencyConflictError,
    PillarIsolationError,
)

ABACUS_PILLARS = {"gridline", "commerce", "trading"}

# Actions that must never execute without explicit human approval
REQUIRES_APPROVAL: dict[str, int] = {
    # task_type pattern fragment   : minimum approval_level
    "seller_outreach":  1,
    "do_not_contact":   1,
    "order_placement":  1,
    "trade_execute":    2,
    "capital_deploy":   2,
}


class AbacusAdapter(FSHAdapterBase):
    """
    Sends FSH tasks to Abacus DeepAgent via REST API.

    Configuration (from environment):
        ABACUS_API_URL          — base URL for DeepAgent API
        ABACUS_API_KEY          — Bearer token
        ABACUS_DEFAULT_TIMEOUT  — seconds (default 120)
        ABACUS_MAX_POLL_WAIT    — NOT USED (blocking poll removed)

    Approval flow (event-driven, replaces blocking poll):
        1. execute() detects approval_level >= 1 → raises ApprovalRequiredError
        2. Orchestrator writes approval_requests row with callback_url (n8n)
        3. Human approves/rejects via UI → DB update triggers pg_notify
        4. n8n picks up notification → POSTs to orchestrator callback
        5. Orchestrator re-enqueues task with approval token embedded
        6. execute() sees approval_token present → proceeds
    """

    adapter_name = "abacus"

    def __init__(
        self,
        api_url:   str | None = None,
        api_key:   str | None = None,
        timeout:   int = 120,
        db_session: Any = None,   # inject psycopg2/asyncpg session for idempotency check
    ):
        self.api_url    = api_url  or os.environ["ABACUS_API_URL"]
        self.api_key    = api_key  or os.environ["ABACUS_API_KEY"]
        self.timeout    = timeout
        self.db_session = db_session

    # ── Phase 1 ───────────────────────────────────────────────────────────────

    def translate_in(self, raw_payload: dict[str, Any]) -> FSHTask:
        task = FSHTask(
            schema_version   = raw_payload.get("schema_version", "1.0.1"),
            task_id          = raw_payload.get("task_id") or self.new_task_id(),
            task_type        = raw_payload["task_type"],
            pillar           = raw_payload["pillar"],
            objective        = raw_payload["objective"],
            approval_level   = int(raw_payload.get("approval_level", 0)),
            execution_engine = raw_payload.get("execution_engine", "abacus"),
            priority         = int(raw_payload.get("priority", 2)),
            compliance_flags = raw_payload.get("compliance_flags", []),
            idempotency_key  = raw_payload.get("idempotency_key"),
            context_artifacts= raw_payload.get("context_artifacts", []),
            expires_at       = raw_payload.get("expires_at"),
            retry_policy     = raw_payload.get("retry_policy", {}),
            parent_task_id   = raw_payload.get("parent_task_id"),
            # ── approval continuation token ───────────────────────────────
            # injected by orchestrator after human approval; not in schema
        )
        task.validate()

        if task.pillar not in ABACUS_PILLARS:
            raise PillarIsolationError(
                f"AbacusAdapter handles {ABACUS_PILLARS}, received pillar='{task.pillar}'"
            )

        self._check_idempotency(task)
        return task

    # ── Phase 2 ───────────────────────────────────────────────────────────────

    def execute(self, task: FSHTask, approval_token: str | None = None) -> FSHTaskResult:
        """
        Execute the task.

        If the task requires human approval and no approval_token is present,
        raises ApprovalRequiredError immediately — NO blocking poll.

        Parameters
        ----------
        task           : canonical FSHTask
        approval_token : opaque token set by orchestrator after human approves;
                         its presence signals that approval has already been granted
        """
        # ── Approval gate ──────────────────────────────────────────────────
        required_level = self._required_approval_level(task)
        if required_level > 0 and approval_token is None:
            self.audit_log(task, "approval_required", {
                "required_level": required_level,
                "compliance_flags": task.compliance_flags,
            })
            # ⚠️  RAISE — do NOT block/poll
            raise ApprovalRequiredError(
                task_id        = task.task_id,
                approval_level = required_level,
                reason         = (
                    f"Task type '{task.task_type}' with compliance_flags "
                    f"{task.compliance_flags} requires approval_level={required_level}. "
                    f"An approval_request row has been created. Execution will resume "
                    f"via n8n callback once a decision is recorded."
                ),
            )

        # ── Build Abacus API request ───────────────────────────────────────
        payload = self._build_abacus_payload(task, approval_token)
        self.audit_log(task, "abacus_request_start", {"pillar": task.pillar})

        try:
            response = requests.post(
                f"{self.api_url}/v1/agents/run",
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type":  "application/json",
                    "X-Task-Id":     task.task_id,
                    "X-Idempotency-Key": task.idempotency_key or "",
                },
                json    = payload,
                timeout = self.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            return FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = f"Abacus API timed out after {self.timeout}s",
                error_detail   = {"error_class": "timeout", "timeout_seconds": self.timeout},
            )
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response else None
            return FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = f"Abacus API HTTP error: {status_code}",
                error_detail   = {
                    "error_class": "http_error",
                    "status_code": status_code,
                    "body":        (exc.response.text[:500] if exc.response else ""),
                },
            )

        data = response.json()
        self.audit_log(task, "abacus_request_end", {"status": data.get("status")})

        return FSHTaskResult(
            task_id        = task.task_id,
            success        = data.get("status") == "completed",
            result_summary = data.get("summary", ""),
            artifacts      = data.get("artifacts", []),
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

    # ── Private helpers ───────────────────────────────────────────────────────

    def _required_approval_level(self, task: FSHTask) -> int:
        """
        Determine minimum approval level needed.
        Takes the maximum of: task.approval_level, REQUIRES_APPROVAL pattern match.
        """
        pattern_level = 0
        for pattern, level in REQUIRES_APPROVAL.items():
            if pattern in task.task_type:
                pattern_level = max(pattern_level, level)

        # financial / external_action flags always require at least level 1
        if {"financial", "external_action"} & set(task.compliance_flags):
            pattern_level = max(pattern_level, 1)

        return max(task.approval_level, pattern_level)

    def _check_idempotency(self, task: FSHTask) -> None:
        """Verify idempotency_key hasn't been used for a different payload."""
        if not task.idempotency_key or not self.db_session:
            return
        # Stub: real implementation queries tasks table
        # existing = self.db_session.query_one(
        #     "SELECT task_id FROM tasks WHERE idempotency_key = %s",
        #     (task.idempotency_key,)
        # )
        # if existing and existing["task_id"] != task.task_id:
        #     raise IdempotencyConflictError(...)
        pass

    def _build_abacus_payload(self, task: FSHTask, approval_token: str | None) -> dict:
        return {
            "agent_id":        f"fsh-{task.pillar}",
            "task_id":         task.task_id,
            "task_type":       task.task_type,
            "objective":       task.objective,
            "context":         task.context_artifacts,
            "compliance_flags": task.compliance_flags,
            "approval_token":  approval_token,
            "priority":        task.priority,
        }
