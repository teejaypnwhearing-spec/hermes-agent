"""
FSH Command Center — Claude Adapter (Reasoning Hub)
====================================================
Routes tasks to Anthropic Claude for analysis, planning, and reasoning.
Pillars: logic, content, forge, gridline (analysis leg)

Claude acts as the central reasoning hub in FSH's hub-and-spoke topology.
It handles PLAN and REVIEW phases; specialist adapters handle EXECUTE.

Review-ref: fsh_architecture_review.md §ARCHITECTURE ASSESSMENT — Hub-and-Spoke
"""
from __future__ import annotations

import os
from typing import Any

import anthropic  # type: ignore  # install: pip install anthropic

from .base import (
    FSHAdapterBase,
    FSHTask,
    FSHTaskResult,
    ApprovalRequiredError,
    PillarIsolationError,
)

CLAUDE_PILLARS = {"logic", "content", "forge", "gridline"}

# System prompt template — Claude's role as FSH reasoning hub
_SYSTEM_PROMPT = """\
You are the FSH Command Center reasoning hub. You operate within a multi-agent \
system for Fermier Sovereign Holdings. Your role is PLAN and REVIEW — you analyse \
tasks, generate structured execution plans, and review outputs for quality and \
compliance. You do NOT execute external actions directly.

Active pillar: {pillar}
Compliance flags in effect: {compliance_flags}
Task type: {task_type}

Respond with structured JSON containing:
  plan_steps   : list of ordered execution steps
  reasoning    : brief explanation of your approach
  risks        : list of identified risks
  review_notes : (if reviewing) quality and compliance observations
"""


class ClaudeAdapter(FSHAdapterBase):
    """
    Sends FSH tasks to Anthropic Claude API.

    Configuration (from environment):
        ANTHROPIC_API_KEY   — Anthropic API key
        CLAUDE_MODEL        — model name (default: claude-opus-4-5)
        CLAUDE_MAX_TOKENS   — max response tokens (default: 4096)
    """

    adapter_name = "claude"

    def __init__(
        self,
        api_key:    str | None = None,
        model:      str | None = None,
        max_tokens: int = 4096,
    ):
        self.api_key    = api_key    or os.environ["ANTHROPIC_API_KEY"]
        self.model      = model      or os.environ.get("CLAUDE_MODEL", "claude-opus-4-5")
        self.max_tokens = max_tokens
        self._client    = anthropic.Anthropic(api_key=self.api_key)

    # ── Phase 1 ───────────────────────────────────────────────────────────────

    def translate_in(self, raw_payload: dict[str, Any]) -> FSHTask:
        task = FSHTask(
            schema_version   = raw_payload.get("schema_version", "1.0.1"),
            task_id          = raw_payload.get("task_id") or self.new_task_id(),
            task_type        = raw_payload["task_type"],
            pillar           = raw_payload["pillar"],
            objective        = raw_payload["objective"],
            approval_level   = int(raw_payload.get("approval_level", 0)),
            execution_engine = raw_payload.get("execution_engine", "claude"),
            priority         = int(raw_payload.get("priority", 2)),
            compliance_flags = raw_payload.get("compliance_flags", []),
            idempotency_key  = raw_payload.get("idempotency_key"),
            context_artifacts= raw_payload.get("context_artifacts", []),
            expires_at       = raw_payload.get("expires_at"),
            retry_policy     = raw_payload.get("retry_policy", {}),
            parent_task_id   = raw_payload.get("parent_task_id"),
        )
        task.validate()

        if task.pillar not in CLAUDE_PILLARS:
            raise PillarIsolationError(
                f"ClaudeAdapter handles {CLAUDE_PILLARS}, received pillar='{task.pillar}'"
            )
        return task

    # ── Phase 2 ───────────────────────────────────────────────────────────────

    def execute(self, task: FSHTask) -> FSHTaskResult:
        # Forge pillar: irreversible tasks always require level-2 approval
        if task.pillar == "forge" and "irreversible" in task.compliance_flags:
            if task.approval_level < 2:
                raise ApprovalRequiredError(
                    task_id        = task.task_id,
                    approval_level = 2,
                    reason         = "Forge irreversible tasks require approval_level=2",
                )

        system_prompt = _SYSTEM_PROMPT.format(
            pillar           = task.pillar,
            compliance_flags = ", ".join(task.compliance_flags) or "none",
            task_type        = task.task_type,
        )

        user_message = self._build_user_message(task)
        self.audit_log(task, "claude_request_start", {"model": self.model})

        try:
            response = self._client.messages.create(
                model      = self.model,
                max_tokens = self.max_tokens,
                system     = system_prompt,
                messages   = [{"role": "user", "content": user_message}],
            )
        except anthropic.APITimeoutError:
            return FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = "Claude API timed out",
                error_detail   = {"error_class": "timeout"},
            )
        except anthropic.APIStatusError as exc:
            return FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = f"Claude API error: {exc.status_code}",
                error_detail   = {
                    "error_class": "api_error",
                    "status_code": exc.status_code,
                    "message":     str(exc.message)[:500],
                },
            )

        content = response.content[0].text if response.content else ""
        self.audit_log(task, "claude_request_end",
                       {"stop_reason": response.stop_reason,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens})

        return FSHTaskResult(
            task_id        = task.task_id,
            success        = response.stop_reason == "end_turn",
            result_summary = content[:4000],
            artifacts      = [{"type": "text", "label": "claude_response", "content": content}],
        )

    # ── Phase 3 ───────────────────────────────────────────────────────────────

    def translate_out(self, result: FSHTaskResult) -> dict[str, Any]:
        import json
        # Attempt to parse Claude's JSON response for structured handoff
        parsed = None
        if result.success and result.result_summary:
            try:
                # Claude sometimes wraps JSON in markdown fences
                text = result.result_summary.strip()
                if text.startswith("```"):
                    text = "\n".join(text.split("\n")[1:-1])
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed = None

        return {
            "task_id":        result.task_id,
            "adapter":        self.adapter_name,
            "success":        result.success,
            "result_summary": result.result_summary,
            "structured":     parsed,
            "artifacts":      result.artifacts,
            "error_detail":   result.error_detail,
            "completed_at":   result.completed_at,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_user_message(self, task: FSHTask) -> str:
        import json
        parts = [
            f"## Task: {task.task_type}",
            f"**Objective:** {task.objective}",
        ]
        if task.context_artifacts:
            parts.append(f"**Context:**\n```json\n{json.dumps(task.context_artifacts, indent=2)}\n```")
        if task.compliance_flags:
            parts.append(f"**Active compliance flags:** {', '.join(task.compliance_flags)}")
        parts.append("\nProvide your analysis as structured JSON.")
        return "\n\n".join(parts)
