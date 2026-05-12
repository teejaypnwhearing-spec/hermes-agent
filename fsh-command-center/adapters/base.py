"""
FSH Command Center — Adapter Base Contract
==========================================
All pillar adapters MUST subclass FSHAdapterBase and implement
the three-phase contract:  translate_in → execute → translate_out

Review-ref: fsh_architecture_review.md §CRITICAL GAPS, R3
"""
from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── Canonical task envelope (mirrors task_schema_v1.0.1.json) ────────────────

@dataclass
class FSHTask:
    schema_version:     str
    task_id:            str
    task_type:          str          # [FIX] required field — was missing in v1.0.0
    pillar:             str
    objective:          str
    approval_level:     int          # 0 | 1 | 2
    execution_engine:   str
    priority:           int = 2      # 1=critical, 2=standard, 3=low
    compliance_flags:   list[str] = field(default_factory=list)
    idempotency_key:    str | None = None
    context_artifacts:  list[dict]  = field(default_factory=list)
    expires_at:         str | None = None
    retry_policy:       dict = field(default_factory=lambda: {
        "max_attempts": 3,
        "backoff_strategy": "exponential",
        "retry_on": ["timeout", "rate_limit"],
    })
    parent_task_id:     str | None = None

    # ── Validation ──────────────────────────────────────────────────────────

    def validate(self) -> None:
        """Raise ValueError for any contract violation."""
        import re
        if not re.match(r"^[a-z][a-z0-9_]*$", self.task_type):
            raise ValueError(f"task_type '{self.task_type}' must match ^[a-z][a-z0-9_]*$")
        if self.approval_level not in (0, 1, 2):
            raise ValueError(f"approval_level must be 0, 1, or 2 — got {self.approval_level}")
        if self.priority not in (1, 2, 3):
            raise ValueError(f"priority must be 1, 2, or 3 — got {self.priority}")
        # Idempotency key required for external / financial operations
        high_risk = {"external_action", "financial"}
        if high_risk & set(self.compliance_flags) and not self.idempotency_key:
            raise ValueError(
                f"idempotency_key is required when compliance_flags contains "
                f"{high_risk & set(self.compliance_flags)}"
            )


@dataclass
class FSHTaskResult:
    task_id:        str
    success:        bool
    result_summary: str
    artifacts:      list[dict] = field(default_factory=list)
    error_detail:   dict | None = None
    completed_at:   str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Abstract base ─────────────────────────────────────────────────────────────

class FSHAdapterBase(abc.ABC):
    """
    Base class for all FSH runtime adapters.

    Required override sequence:
        translate_in(raw)   → FSHTask
        execute(task)       → FSHTaskResult
        translate_out(result) → dict
    """

    adapter_name: str = "base"

    # ── Phase 1: translate incoming payload to canonical FSHTask ────────────

    @abc.abstractmethod
    def translate_in(self, raw_payload: dict[str, Any]) -> FSHTask:
        """
        Validate and normalise an incoming payload into the canonical FSHTask.
        MUST call task.validate() before returning.
        """

    # ── Phase 2: execute the task inside this runtime ───────────────────────

    @abc.abstractmethod
    def execute(self, task: FSHTask) -> FSHTaskResult:
        """
        Run the task.  Must NOT block the calling thread for approval waits.
        For approval_level >= 1, raise ApprovalRequiredError instead.
        """

    # ── Phase 3: translate result back to a serialisable envelope ───────────

    @abc.abstractmethod
    def translate_out(self, result: FSHTaskResult) -> dict[str, Any]:
        """Convert FSHTaskResult to the wire envelope consumed by n8n / Hermes."""

    # ── Shared helpers ───────────────────────────────────────────────────────

    def new_task_id(self) -> str:
        return str(uuid.uuid4())

    def audit_log(self, task: FSHTask, action: str, detail: dict | None = None) -> None:
        """
        Write to audit_trail.  Concrete adapters should inject a DB session;
        this default implementation logs to stdout for test environments.
        """
        print(
            f"[AUDIT] {datetime.now(timezone.utc).isoformat()} "
            f"adapter={self.adapter_name} task_id={task.task_id} "
            f"pillar={task.pillar} action={action} detail={detail or {}}"
        )


# ── Exceptions ────────────────────────────────────────────────────────────────

class ApprovalRequiredError(Exception):
    """
    Raised by execute() when a task requires human approval before proceeding.
    The caller (orchestrator) must create an approval_request row and return
    a 202-style 'pending_approval' response — NOT block or poll.
    """
    def __init__(self, task_id: str, approval_level: int, reason: str):
        self.task_id        = task_id
        self.approval_level = approval_level
        self.reason         = reason
        super().__init__(f"Approval level {approval_level} required for task {task_id}: {reason}")


class IdempotencyConflictError(Exception):
    """Raised when an idempotency_key already exists for a different task payload."""


class PillarIsolationError(Exception):
    """Raised when a task's pillar does not match the adapter's configured pillar scope."""
