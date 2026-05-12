"""
FSH Command Center — Hermes Adapter
====================================
Routes tasks to the local Hermes skill library.
Pillar: content, gridline (skill execution leg)

Review-ref: fsh_architecture_review.md §CRITICAL GAPS — adapter specifications
"""
from __future__ import annotations

import json
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


class HermesAdapter(FSHAdapterBase):
    """
    Executes FSH tasks by invoking Hermes CLI skills.

    translate_in  → validates + maps task_type to a skill name
    execute       → calls `hermes run <skill>` as a subprocess
    translate_out → wraps stdout/stderr into FSHTaskResult envelope
    """

    adapter_name = "hermes"

    def __init__(self, skills_root: str | Path = "skills", hermes_bin: str = "hermes"):
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

    def execute(self, task: FSHTask) -> FSHTaskResult:
        # Approval gate — do NOT proceed if human sign-off required
        if task.approval_level >= 1:
            skill_name = self._resolve_skill(task.task_type)
            if any(skill_name.startswith(p) for p in PROTECTED_SKILL_PREFIXES):
                self.audit_log(task, "approval_required",
                               {"skill": skill_name, "approval_level": task.approval_level})
                raise ApprovalRequiredError(
                    task_id        = task.task_id,
                    approval_level = task.approval_level,
                    reason         = f"Skill '{skill_name}' requires human approval",
                )

        skill_name = self._resolve_skill(task.task_type)
        skill_path = self._locate_skill(skill_name)

        env_overrides = {
            "FSH_TASK_ID":    task.task_id,
            "FSH_PILLAR":     task.pillar,
            "FSH_TASK_TYPE":  task.task_type,
            "FSH_OBJECTIVE":  task.objective,
        }

        self.audit_log(task, "hermes_skill_start", {"skill": skill_name})

        try:
            result = subprocess.run(
                [self.hermes_bin, "run", str(skill_path),
                 "--input", json.dumps({"objective": task.objective,
                                        "context":   task.context_artifacts})],
                capture_output = True,
                text           = True,
                timeout        = 300,
                env            = {**__import__("os").environ, **env_overrides},
            )
        except subprocess.TimeoutExpired:
            return FSHTaskResult(
                task_id        = task.task_id,
                success        = False,
                result_summary = "Hermes skill timed out after 300s",
                error_detail   = {"error_class": "timeout", "skill": skill_name},
            )

        success = result.returncode == 0
        self.audit_log(task, "hermes_skill_end", {"skill": skill_name,
                                                   "exit_code": result.returncode})
        return FSHTaskResult(
            task_id        = task.task_id,
            success        = success,
            result_summary = result.stdout.strip()[:2000] if success else result.stderr.strip()[:500],
            error_detail   = None if success else {
                "error_class": "execution_error",
                "returncode":  result.returncode,
                "stderr":      result.stderr[:500],
            },
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
