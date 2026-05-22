"""
FSH Command Center - Hermes Adapter
===================================
Routes tasks to the local Hermes skill library.
Pillar: content, gridline (skill execution leg)

Review-ref: fsh_architecture_review.md CRITICAL GAPS - adapter specifications
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

API_KEY_ENV_VARS = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")


class HermesAdapter(FSHAdapterBase):
    """
    Executes FSH tasks by invoking Hermes CLI skills.

    translate_in  -> validates + maps task_type to a skill name
    execute       -> calls hermes-agent with a query subprocess
    translate_out -> wraps stdout/stderr into FSHTaskResult envelope
    """

    adapter_name = "hermes"

    def __init__(
        self,
        skills_root: str | Path = "skills",
        hermes_bin: str = "hermes-agent",
        provider: str | None = None,
        max_turns: int = 5,
        timeout_seconds: int = 300,
    ):
        self.skills_root = Path(skills_root)
        self.hermes_bin = hermes_bin
        self.provider = provider
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds

    # -- Phase 1 ---------------------------------------------------------------

    def translate_in(self, raw_payload: dict[str, Any]) -> FSHTask:
        task = FSHTask(
            schema_version=raw_payload.get("schema_version", "1.0.1"),
            task_id=raw_payload.get("task_id") or self.new_task_id(),
            task_type=raw_payload["task_type"],
            pillar=raw_payload["pillar"],
            objective=raw_payload["objective"],
            approval_level=int(raw_payload.get("approval_level", 0)),
            execution_engine=raw_payload.get("execution_engine", "hermes"),
            priority=int(raw_payload.get("priority", 2)),
            compliance_flags=raw_payload.get("compliance_flags", []),
            idempotency_key=raw_payload.get("idempotency_key"),
            context_artifacts=raw_payload.get("context_artifacts", []),
            expires_at=raw_payload.get("expires_at"),
            retry_policy=raw_payload.get("retry_policy", {}),
            parent_task_id=raw_payload.get("parent_task_id"),
        )
        task.validate()

        if task.pillar not in HERMES_PILLARS:
            raise PillarIsolationError(
                f"HermesAdapter handles {HERMES_PILLARS}, received pillar='{task.pillar}'"
            )
        return task

    # -- Phase 2 ---------------------------------------------------------------

    def execute(self, task: FSHTask, approval_token: str | None = None) -> FSHTaskResult:
        skill_name = self._resolve_skill(task.task_type)

        # Approval gate - do not proceed if human sign-off is required.
        if task.approval_level >= 1 and any(
            skill_name.startswith(prefix) for prefix in PROTECTED_SKILL_PREFIXES
        ):
            self.audit_log(
                task,
                "approval_required",
                {"skill": skill_name, "approval_level": task.approval_level},
            )
            raise ApprovalRequiredError(
                task_id=task.task_id,
                approval_level=task.approval_level,
                reason=f"Skill '{skill_name}' requires human approval",
            )

        try:
            skill_path = self._locate_skill(skill_name)
        except FileNotFoundError as exc:
            return FSHTaskResult(
                task_id=task.task_id,
                success=False,
                result_summary=str(exc),
                error_detail={"error_class": "skill_not_found", "skill": skill_name},
            )

        env = self._build_env(task, approval_token)
        if not self._has_api_key(env):
            return FSHTaskResult(
                task_id=task.task_id,
                success=False,
                result_summary="Hermes API key is missing",
                error_detail={
                    "error_class": "missing_api_key",
                    "required_any_of": list(API_KEY_ENV_VARS),
                },
            )

        command = self._build_command(task, skill_name, skill_path)
        self.audit_log(task, "hermes_skill_start", {"skill": skill_name})

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=env,
            )
        except FileNotFoundError:
            return FSHTaskResult(
                task_id=task.task_id,
                success=False,
                result_summary=f"Hermes binary not found: {self.hermes_bin}",
                error_detail={
                    "error_class": "binary_not_found",
                    "binary": self.hermes_bin,
                    "skill": skill_name,
                },
            )
        except subprocess.TimeoutExpired:
            return FSHTaskResult(
                task_id=task.task_id,
                success=False,
                result_summary=f"Hermes skill timed out after {self.timeout_seconds}s",
                error_detail={
                    "error_class": "timeout",
                    "skill": skill_name,
                    "timeout_seconds": self.timeout_seconds,
                },
            )
        except subprocess.SubprocessError as exc:
            return FSHTaskResult(
                task_id=task.task_id,
                success=False,
                result_summary="Hermes subprocess failed before completion",
                error_detail={
                    "error_class": "subprocess_error",
                    "skill": skill_name,
                    "message": str(exc),
                },
            )

        success = result.returncode == 0
        self.audit_log(
            task,
            "hermes_skill_end",
            {"skill": skill_name, "exit_code": result.returncode},
        )

        if success:
            summary = self._parse_hermes_output(result.stdout)[:2000]
            error_detail = None
        else:
            summary = (result.stderr.strip() or result.stdout.strip())[:500]
            error_detail = {
                "error_class": "execution_error",
                "returncode": result.returncode,
                "stderr": result.stderr[:500],
            }

        return FSHTaskResult(
            task_id=task.task_id,
            success=success,
            result_summary=summary,
            error_detail=error_detail,
        )

    # -- Phase 3 ---------------------------------------------------------------

    def translate_out(self, result: FSHTaskResult) -> dict[str, Any]:
        return {
            "task_id": result.task_id,
            "adapter": self.adapter_name,
            "success": result.success,
            "result_summary": result.result_summary,
            "artifacts": result.artifacts,
            "error_detail": result.error_detail,
            "completed_at": result.completed_at,
        }

    # -- Private helpers -------------------------------------------------------

    def _resolve_skill(self, task_type: str) -> str:
        """Map task_type snake_case to hermes skill kebab-case."""
        return task_type.replace("_", "-")

    def _locate_skill(self, skill_name: str) -> Path:
        """Recursively find the SKILL.md for a given skill name."""
        for md in self.skills_root.rglob("SKILL.md"):
            if md.parent.name == skill_name:
                return md.parent
        raise FileNotFoundError(
            f"No Hermes skill found for '{skill_name}' under {self.skills_root}"
        )

    def _build_command(self, task: FSHTask, skill_name: str, skill_path: Path) -> list[str]:
        query = self._build_query(task, skill_name, skill_path)
        command = [
            self.hermes_bin,
            "--query",
            query,
            "--max-turns",
            str(self.max_turns),
        ]
        if self.provider:
            command.extend(["--provider", self.provider])
        return command

    def _build_query(self, task: FSHTask, skill_name: str, skill_path: Path) -> str:
        payload = {
            "objective": task.objective,
            "context_artifacts": task.context_artifacts,
        }
        return (
            f"Execute FSH skill '{skill_name}' at '{skill_path}'. "
            f"Task payload: {json.dumps(payload, sort_keys=True)}"
        )

    def _build_env(self, task: FSHTask, approval_token: str | None) -> dict[str, str]:
        env = {**os.environ}
        env.update(
            {
                "FSH_TASK_ID": task.task_id,
                "FSH_PILLAR": task.pillar,
                "FSH_TASK_TYPE": task.task_type,
                "FSH_OBJECTIVE": task.objective,
            }
        )
        if approval_token:
            env["FSH_APPROVAL_TOKEN"] = approval_token
        return env

    def _has_api_key(self, env: dict[str, str]) -> bool:
        return any(env.get(name) for name in API_KEY_ENV_VARS)

    def _parse_hermes_output(self, stdout: str) -> str:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        noise_prefixes = ("[INFO]", "[DEBUG]", "[TRACE]", "Starting", "Running")
        meaningful = [
            line for line in lines if not line.startswith(noise_prefixes)
        ]
        return "\n".join(meaningful or lines).strip()
