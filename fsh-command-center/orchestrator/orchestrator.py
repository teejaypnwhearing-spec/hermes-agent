"""
FSH Command Center — Task Orchestrator
=======================================
The orchestrator is the entry point for all FSH tasks. It:
  1. Applies pillar defaults to raw payloads
  2. Routes to the correct adapter based on execution_engine
  3. Handles ApprovalRequiredError → creates approval_request row
  4. Logs to audit_trail
  5. Manages retry_queue and dead_letter_queue promotion
  6. Fires n8n callbacks for async notifications

This is the PLAN→EXECUTE→REVIEW→LOG→HANDOFF state machine.

Usage:
    orchestrator = FSHOrchestrator(db_url=os.environ["DATABASE_URL"])
    result = orchestrator.submit(raw_task_dict)
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
import requests

import sys as _sys
import os as _os
_FSH_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _FSH_ROOT not in _sys.path:
    _sys.path.insert(0, _FSH_ROOT)

from adapters import (
    AbacusAdapter, ClaudeAdapter, HermesAdapter, ManusAdapter,
    FSHTask, FSHTaskResult, ApprovalRequiredError,
)
from config.pillar_defaults import apply_defaults

# ── Engine → Adapter mapping ──────────────────────────────────────────────────
_ADAPTER_MAP = {
    "abacus": AbacusAdapter,
    "claude":  ClaudeAdapter,
    "hermes":  HermesAdapter,
    "manus":   ManusAdapter,
}

# ── Task lifecycle phases ─────────────────────────────────────────────────────
PHASE_QUEUED             = "queued"
PHASE_EXECUTING          = "executing"
PHASE_AWAITING_APPROVAL  = "awaiting_approval"
PHASE_COMPLETED          = "completed"
PHASE_FAILED             = "failed"


class FSHOrchestrator:
    """
    Central task router for the FSH Command Center.

    Parameters
    ----------
    db_url       : Postgres connection string (DATABASE_URL)
    n8n_base_url : n8n webhook base URL for async callbacks
    dry_run      : if True, translate_in + approval checks run but execute() is skipped
    """

    def __init__(
        self,
        db_url:       str | None = None,
        n8n_base_url: str | None = None,
        dry_run:      bool = False,
    ):
        self.db_url       = db_url       or os.environ.get("DATABASE_URL")
        self.n8n_base_url = n8n_base_url or os.environ.get("N8N_BASE_URL", "")
        self.dry_run      = dry_run
        self._conn        = None

    # ── Public API ────────────────────────────────────────────────────────────

    def submit(
        self,
        raw_payload:    dict[str, Any],
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Submit a task for execution.

        Parameters
        ----------
        raw_payload    : raw task dict (will have pillar defaults merged in)
        approval_token : provided by n8n callback after human approval

        Returns
        -------
        dict with keys: task_id, status, result | approval_request_id | error
        """
        # 1. Merge pillar defaults
        payload = apply_defaults(raw_payload)

        # 2. Select adapter
        engine = payload.get("execution_engine", "hermes")
        AdapterClass = _ADAPTER_MAP.get(engine)
        if not AdapterClass:
            return self._error_response(
                payload.get("task_id", str(uuid.uuid4())),
                f"Unknown execution_engine: '{engine}'"
            )

        adapter = self._build_adapter(engine, AdapterClass)

        # 3. translate_in → FSHTask
        try:
            task = adapter.translate_in(payload)
        except (ValueError, KeyError) as exc:
            return self._error_response(payload.get("task_id", ""), str(exc))

        # 4. Persist task as queued
        if self.db_url:
            self._upsert_task(task, PHASE_QUEUED)
            self._audit(task, "task_submitted", {"engine": engine, "dry_run": self.dry_run})

        # 5. Execute
        if self.dry_run:
            return {"task_id": task.task_id, "status": "dry_run", "task": _task_dict(task)}

        try:
            self._update_task_status(task.task_id, PHASE_EXECUTING)
            # Pass approval_token to adapters that support it (Abacus)
            if approval_token and hasattr(adapter, "execute"):
                import inspect
                sig = inspect.signature(adapter.execute)
                if "approval_token" in sig.parameters:
                    result = adapter.execute(task, approval_token=approval_token)
                else:
                    result = adapter.execute(task)
            else:
                result = adapter.execute(task)

        except ApprovalRequiredError as apr:
            return self._handle_approval_required(task, apr)

        except Exception as exc:
            err_result = FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = f"Unhandled error: {type(exc).__name__}",
                error_detail   = {"error_class": type(exc).__name__, "message": str(exc)[:500]},
            )
            self._finalize(task, err_result, adapter)
            return self._error_response(task.task_id, str(exc), result=err_result)

        # 6. Finalize
        self._finalize(task, result, adapter)

        return {
            "task_id":        result.task_id,
            "status":         PHASE_COMPLETED if result.success else PHASE_FAILED,
            "success":        result.success,
            "result_summary": result.result_summary,
            "artifacts":      result.artifacts,
            "error_detail":   result.error_detail,
            "completed_at":   result.completed_at,
        }

    def resume_after_approval(
        self,
        task_id:        str,
        approval_token: str,
    ) -> dict[str, Any]:
        """
        Called by n8n webhook after human approves a task.
        Reloads the task from DB and re-submits with the approval token.
        """
        if not self.db_url:
            return self._error_response(task_id, "No DATABASE_URL configured")

        task_row = self._fetch_task(task_id)
        if not task_row:
            return self._error_response(task_id, f"Task {task_id} not found in DB")

        return self.submit(task_row, approval_token=approval_token)

    # ── Private — adapter factory ─────────────────────────────────────────────

    def _build_adapter(self, engine: str, AdapterClass) -> Any:
        """
        Instantiate adapter. In dry_run mode uses placeholder credentials
        so validation/translate_in can run without real API keys.
        """
        if self.dry_run or engine == "hermes":
            try:
                return AdapterClass()
            except (KeyError, TypeError):
                # Fallback: inject placeholder creds for dry-run validation
                pass

        # Adapters that require explicit credentials
        if engine == "abacus":
            return AdapterClass(
                api_url = os.environ.get("ABACUS_API_URL", "http://localhost"),
                api_key = os.environ.get("ABACUS_API_KEY", "dry-run-key"),
            )
        if engine == "claude":
            return AdapterClass(
                api_key = os.environ.get("ANTHROPIC_API_KEY", "dry-run-key"),
            )
        if engine == "manus":
            return AdapterClass(
                api_url = os.environ.get("MANUS_API_URL", "http://localhost"),
                api_key = os.environ.get("MANUS_API_KEY", "dry-run-key"),
            )
        return AdapterClass()



    def _handle_approval_required(
        self, task: FSHTask, exc: ApprovalRequiredError
    ) -> dict[str, Any]:
        approval_id = str(uuid.uuid4())
        callback_url = (
            f"{self.n8n_base_url}/webhook/fsh-approval"
            if self.n8n_base_url else None
        )

        if self.db_url:
            self._create_approval_request(task, exc, approval_id, callback_url)
            self._update_task_status(task.task_id, PHASE_AWAITING_APPROVAL)
            self._audit(task, "approval_requested", {
                "approval_id":    approval_id,
                "approval_level": exc.approval_level,
                "reason":         exc.reason,
            })

        # Fire n8n notification webhook if configured
        if callback_url:
            self._notify_n8n_approval_needed(task, approval_id, exc, callback_url)

        return {
            "task_id":           task.task_id,
            "status":            PHASE_AWAITING_APPROVAL,
            "approval_id":       approval_id,
            "approval_level":    exc.approval_level,
            "reason":            exc.reason,
            "message":           (
                f"Task requires approval_level={exc.approval_level}. "
                f"An approval request has been created (ID: {approval_id}). "
                f"Execution will resume automatically via n8n callback once approved."
            ),
        }

    def _notify_n8n_approval_needed(
        self, task: FSHTask, approval_id: str,
        exc: ApprovalRequiredError, callback_url: str
    ) -> None:
        try:
            requests.post(
                f"{self.n8n_base_url}/webhook/fsh-approval-needed",
                json={
                    "approval_id":    approval_id,
                    "task_id":        task.task_id,
                    "pillar":         task.pillar,
                    "task_type":      task.task_type,
                    "objective":      task.objective,
                    "approval_level": exc.approval_level,
                    "reason":         exc.reason,
                    "callback_url":   callback_url,
                },
                timeout=10,
            )
        except Exception:
            pass  # notification failure must never block task submission

    # ── Private — DB helpers ──────────────────────────────────────────────────

    def _get_conn(self):
        if not self.db_url:
            return None
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.db_url)
        return self._conn

    def _upsert_task(self, task: FSHTask, status: str) -> None:
        conn = self._get_conn()
        if not conn:
            return
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tasks
                    (task_id, schema_version, task_type, pillar, objective,
                     priority, approval_level, execution_engine, compliance_flags,
                     idempotency_key, context_artifacts, retry_policy,
                     parent_task_id, status, expires_at)
                VALUES
                    (%(task_id)s, %(schema_version)s, %(task_type)s, %(pillar)s,
                     %(objective)s, %(priority)s, %(approval_level)s::approval_level_enum,
                     %(execution_engine)s::execution_engine_enum,
                     %(compliance_flags)s, %(idempotency_key)s,
                     %(context_artifacts)s::jsonb, %(retry_policy)s::jsonb,
                     %(parent_task_id)s, %(status)s::task_status_enum, %(expires_at)s)
                ON CONFLICT (task_id) DO NOTHING
            """, {
                "task_id":           task.task_id,
                "schema_version":    task.schema_version,
                "task_type":         task.task_type,
                "pillar":            task.pillar,
                "objective":         task.objective,
                "priority":          task.priority,
                "approval_level":    str(task.approval_level),
                "execution_engine":  task.execution_engine,
                "compliance_flags":  task.compliance_flags,
                "idempotency_key":   task.idempotency_key,
                "context_artifacts": json.dumps(task.context_artifacts),
                "retry_policy":      json.dumps(task.retry_policy),
                "parent_task_id":    task.parent_task_id,
                "status":            status,
                "expires_at":        task.expires_at,
            })
        conn.commit()

    def _update_task_status(self, task_id: str, status: str) -> None:
        conn = self._get_conn()
        if not conn:
            return
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET status = %s::task_status_enum WHERE task_id = %s",
                (status, task_id)
            )
        conn.commit()

    def _fetch_task(self, task_id: str) -> dict | None:
        conn = self._get_conn()
        if not conn:
            return None
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tasks WHERE task_id = %s", (task_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def _create_approval_request(
        self, task: FSHTask, exc: ApprovalRequiredError,
        approval_id: str, callback_url: str | None
    ) -> None:
        conn = self._get_conn()
        if not conn:
            return
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO approval_requests
                    (approval_id, task_id, requested_by, approval_level, reason,
                     context_snapshot, callback_url)
                VALUES (%s, %s, %s, %s::approval_level_enum, %s, %s::jsonb, %s)
            """, (
                approval_id, task.task_id, task.execution_engine,
                str(exc.approval_level), exc.reason,
                json.dumps({"task_type": task.task_type, "pillar": task.pillar,
                            "compliance_flags": task.compliance_flags}),
                callback_url,
            ))
        conn.commit()

    def _audit(self, task: FSHTask, action: str, detail: dict) -> None:
        conn = self._get_conn()
        if not conn:
            return
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO audit_trail (task_id, pillar, action, actor, detail)
                VALUES (%s, %s::pillar_enum, %s, %s, %s::jsonb)
            """, (
                task.task_id, task.pillar, action, "orchestrator",
                json.dumps(detail),
            ))
        conn.commit()

    def _finalize(
        self, task: FSHTask, result: FSHTaskResult, adapter: Any
    ) -> None:
        status = PHASE_COMPLETED if result.success else PHASE_FAILED
        conn = self._get_conn()
        if not conn:
            return
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE tasks
                SET status       = %s::task_status_enum,
                    result_summary = %s,
                    error_detail = %s::jsonb,
                    completed_at = NOW()
                WHERE task_id = %s
            """, (
                status,
                result.result_summary[:2000] if result.result_summary else None,
                json.dumps(result.error_detail) if result.error_detail else None,
                task.task_id,
            ))
        conn.commit()
        self._audit(task, f"task_{status}", {
            "success": result.success,
            "error_class": (result.error_detail or {}).get("error_class"),
        })

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _error_response(
        task_id: str, message: str, result: FSHTaskResult | None = None
    ) -> dict:
        return {
            "task_id":   task_id,
            "status":    PHASE_FAILED,
            "success":   False,
            "error":     message,
            "result":    result,
        }


def _task_dict(task: FSHTask) -> dict:
    return {
        "task_id":          task.task_id,
        "task_type":        task.task_type,
        "pillar":           task.pillar,
        "objective":        task.objective,
        "priority":         task.priority,
        "approval_level":   task.approval_level,
        "execution_engine": task.execution_engine,
        "compliance_flags": task.compliance_flags,
    }
