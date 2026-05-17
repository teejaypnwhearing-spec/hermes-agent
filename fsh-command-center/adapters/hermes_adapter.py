"""
FSH Command Center — Hermes Adapter
====================================
Routes tasks to the local Hermes skill library.
Pillar: content, gridline (skill execution leg)

Review-ref: fsh_architecture_review.md §CRITICAL GAPS — adapter specifications

P0 Fix (fix/hermes-adapter-p0):
  - Replaced broken `hermes run <skill>` subprocess call with
    `hermes-agent --query` invocation per adapter spec.
  - Fixed default hermes_bin to "hermes-agent" (was "hermes").
  - Added comprehensive error handling for all failure modes.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .base import (
    FSHAdapterBase,
    FSHTask,
    FSHTaskResult,
    ApprovalRequiredError,
    PillarIsolationError,
)

# Skills that require approval_level >= 1 before Hermes can run them
PROTECTED_SKILL_PREFIXES = {
    "gridline-seller-outreach",
    "gridline-do-not-contact",
    "forge-sop-publish",
}

# Allowed pillars for Hermes adapter
HERMES_PILLARS = {"content", "gridline", "forge"}

# Default timeout (seconds) for hermes-agent execution
_DEFAULT_TIMEOUT = 300


class HermesAdapter(FSHAdapterBase):
    """
    Executes FSH tasks by invoking the hermes-agent CLI.

    translate_in  → validates + maps task_type to a skill name
    execute       → calls `hermes-agent --query` as a subprocess
    translate_out → wraps stdout/stderr into FSHTaskResult envelope

    P0 Fix: was incorrectly calling `hermes run <skill>` which does not exist
    in hermes-agent's CLI interface. Corrected to `hermes-agent --query`.
    """

    adapter_name = "hermes"

    def __init__(
        self,
        skills_root: str | Path = "skills",
        hermes_bin: str = "hermes-agent",   # FIX: was "hermes" — binary is "hermes-agent"
    ):
        self.skills_root = Path(skills_root)
        self.hermes_bin  = hermes_bin

    # ── Phase 1 ───────────────────────────────────────────────────────────────

    def translate_in(self, raw_payload: dict[str, Any]) -> FSHTask:
        task = FSHTask(
            schema_version   = raw_payload.get("schema_version", "1.0.1"),
            task_id          = raw_payload.get("task_id") or self.new_task_id(),
            task_type        = raw_payload["task_type"],
            pillar           = raw_payload["pillar"],
            objective        = raw_payload["objective"],
            approval_level   = int(raw_payload.get("approval_level", 0)),
            execution_engine = raw_payload.get("execution_engine", "hermes"),
            priority         = int(raw_payload.get("priority", 2)),
            compliance_flags = raw_payload.get("compliance_flags", []),
            idempotency_key  = raw_payload.get("idempotency_key"),
            context_artifacts= raw_payload.get("context_artifacts", []),
            expires_at       = raw_payload.get("expires_at"),
            retry_policy     = raw_payload.get("retry_policy", {}),
            parent_task_id   = raw_payload.get("parent_task_id"),
        )
        task.validate()

        if task.pillar not in HERMES_PILLARS:
            raise PillarIsolationError(
                f"HermesAdapter handles {HERMES_PILLARS}, received pillar='{task.pillar}'"
            )
        return task

    # ── Phase 2 ───────────────────────────────────────────────────────────────

    def execute(self, task: FSHTask, timeout: int = _DEFAULT_TIMEOUT) -> FSHTaskResult:
        # Approval gate — do NOT proceed if human sign-off required
        skill_name = self._resolve_skill(task.task_type)
        if task.approval_level >= 1:
            if any(skill_name.startswith(p) for p in PROTECTED_SKILL_PREFIXES):
                self.audit_log(task, "approval_required",
                               {"skill": skill_name, "approval_level": task.approval_level})
                raise ApprovalRequiredError(
                    task_id        = task.task_id,
                    approval_level = task.approval_level,
                    reason         = f"Skill '{skill_name}' requires human approval",
                )

        self.audit_log(task, "hermes_skill_start", {"skill": skill_name})

        try:
            result = self._call_hermes_agent(
                query   = task.objective,
                context = {
                    "task_id":          task.task_id,
                    "task_type":        task.task_type,
                    "pillar":           task.pillar,
                    "context_artifacts": task.context_artifacts,
                },
                skill   = skill_name,
                timeout = timeout,
            )
        except RuntimeError as exc:
            error_str = str(exc)
            self.audit_log(task, "hermes_skill_error",
                           {"skill": skill_name, "error": error_str})
            # Map specific error classes for structured error_detail
            if "timed out" in error_str:
                error_class = "timeout"
            elif "exited with code" in error_str:
                error_class = "execution_error"
            elif "binary not found" in error_str.lower():
                error_class = "binary_not_found"
            else:
                error_class = "unknown_error"

            return FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = error_str[:500],
                error_detail   = {
                    "error_class": error_class,
                    "skill":       skill_name,
                    "detail":      error_str,
                },
            )

        self.audit_log(task, "hermes_skill_end",
                       {"skill": skill_name, "success": result.get("success", True)})

        summary = result.get("output") or result.get("result") or json.dumps(result)
        return FSHTaskResult(
            task_id        = task.task_id,
            success        = True,
            result_summary = str(summary)[:2000],
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

    # ── Private helpers ────────────────────────────────────────────────────────

    def _call_hermes_agent(
        self,
        query:   str,
        context: dict,
        skill:   str,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> dict:
        """
        Correct HermesAdapter invocation using hermes-agent --query.

        Replaces the broken `hermes run <skill>` call with the proper
        `hermes-agent --query` interface.

        Args:
            query:   Natural language task instruction
            context: JSON-serializable context dictionary
            skill:   Skill name from registry (kebab-case)
            timeout: Execution timeout in seconds

        Returns:
            Parsed JSON response from hermes-agent

        Raises:
            RuntimeError: On binary not found, non-zero exit, timeout,
                          invalid JSON, or missing required parameters.
        """
        # Validate required parameters before shelling out
        if not query or not query.strip():
            raise RuntimeError(
                "hermes-agent requires a non-empty --query parameter."
            )
        if not skill or not skill.strip():
            raise RuntimeError(
                "hermes-agent requires a non-empty --skill parameter."
            )

        cmd = [
            self.hermes_bin,
            "--query",         query,
            "--context",       json.dumps(context),
            "--output-format", "json",
            "--timeout",       str(timeout),
            "--skill",         skill,
        ]

        env_overrides = {
            "FSH_TASK_ID":    context.get("task_id", ""),
            "FSH_PILLAR":     context.get("pillar", ""),
            "FSH_TASK_TYPE":  context.get("task_type", ""),
            "FSH_OBJECTIVE":  query,
        }
        run_env = {**os.environ, **env_overrides}

        try:
            result = subprocess.run(
                cmd,
                capture_output = True,
                text           = True,
                timeout        = timeout + 10,   # buffer beyond hermes-agent's own timeout
                check          = False,
                env            = run_env,
            )

        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"hermes-agent timed out after {timeout}s for skill: {skill}"
            )

        except FileNotFoundError:
            raise RuntimeError(
                f"hermes-agent binary not found at '{self.hermes_bin}'. "
                f"Verify installation and PATH environment variable. "
                f"Install with: pip install hermes-agent"
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"hermes-agent exited with code {result.returncode} "
                f"for skill '{skill}': {result.stderr.strip()[:500]}"
            )

        if not result.stdout.strip():
            raise RuntimeError(
                f"hermes-agent returned empty output for skill '{skill}'"
            )

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"hermes-agent returned non-JSON output for skill '{skill}': "
                f"{result.stdout[:500]}"
            )

    def _resolve_skill(self, task_type: str) -> str:
        """Map task_type snake_case → hermes skill kebab-case."""
        return task_type.replace("_", "-")

    def _locate_skill(self, skill_name: str) -> Path:
        """Recursively find the SKILL.md for a given skill name."""
        for md in self.skills_root.rglob("SKILL.md"):
            if md.parent.name == skill_name:
                return md.parent
        raise FileNotFoundError(
            f"No Hermes skill found for '{skill_name}' under {self.skills_root}"
        )
