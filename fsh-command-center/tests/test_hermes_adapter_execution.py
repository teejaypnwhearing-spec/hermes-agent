"""
HermesAdapter execution regression tests.

These tests protect the command shape and failure handling required for
Hermes-routed FSH tasks.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

FSH_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(FSH_ROOT))

from adapters.base import FSHTask
from adapters.hermes_adapter import HermesAdapter


class TestHermesAdapterExecution:
    def _task(self, **overrides) -> FSHTask:
        defaults = dict(
            schema_version="1.0.1",
            task_id="00000000-0000-0000-0000-000000000099",
            task_type="content_script_gen",
            pillar="content",
            objective="Write a TikTok script",
            approval_level=0,
            execution_engine="hermes",
        )
        defaults.update(overrides)
        return FSHTask(**defaults)

    def _skills_root(self, tmp_path: Path, skill_name: str = "content-script-gen") -> Path:
        skill_dir = tmp_path / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test skill\n", encoding="utf-8")
        return tmp_path

    def _set_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def test_default_binary_is_hermes_agent(self):
        assert HermesAdapter().hermes_bin == "hermes-agent"

    def test_execute_uses_query_command(self, tmp_path, monkeypatch):
        self._set_api_key(monkeypatch)
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout="final output", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        adapter = HermesAdapter(skills_root=self._skills_root(tmp_path))

        result = adapter.execute(self._task())

        assert result.success is True
        assert captured["command"][0] == "hermes-agent"
        assert "--query" in captured["command"]
        assert "--max-turns" in captured["command"]

    def test_execute_never_uses_invalid_run_subcommand(self, tmp_path, monkeypatch):
        self._set_api_key(monkeypatch)
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return SimpleNamespace(returncode=0, stdout="final output", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        adapter = HermesAdapter(skills_root=self._skills_root(tmp_path))

        adapter.execute(self._task())

        assert captured["command"][:2] != ["hermes", "run"]
        assert "run" not in captured["command"]

    def test_missing_api_key_returns_structured_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        def fake_run(*args, **kwargs):
            pytest.fail("subprocess should not run without a provider API key")

        monkeypatch.setattr(subprocess, "run", fake_run)
        adapter = HermesAdapter(skills_root=self._skills_root(tmp_path))

        result = adapter.execute(self._task())

        assert result.success is False
        assert result.error_detail["error_class"] == "missing_api_key"

    def test_binary_not_found_returns_structured_error(self, tmp_path, monkeypatch):
        self._set_api_key(monkeypatch)

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("missing binary")

        monkeypatch.setattr(subprocess, "run", fake_run)
        adapter = HermesAdapter(skills_root=self._skills_root(tmp_path))

        result = adapter.execute(self._task())

        assert result.success is False
        assert result.error_detail["error_class"] == "binary_not_found"

    def test_timeout_returns_structured_error(self, tmp_path, monkeypatch):
        self._set_api_key(monkeypatch)

        def fake_run(command, **kwargs):
            raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs["timeout"])

        monkeypatch.setattr(subprocess, "run", fake_run)
        adapter = HermesAdapter(skills_root=self._skills_root(tmp_path), timeout_seconds=17)

        result = adapter.execute(self._task())

        assert result.success is False
        assert result.error_detail["error_class"] == "timeout"
        assert result.error_detail["timeout_seconds"] == 17

    def test_provider_flag_is_passed_to_command(self, tmp_path, monkeypatch):
        self._set_api_key(monkeypatch)
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return SimpleNamespace(returncode=0, stdout="final output", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        adapter = HermesAdapter(
            skills_root=self._skills_root(tmp_path),
            provider="openrouter/anthropic/claude-3.5-sonnet",
        )

        adapter.execute(self._task())

        assert "--provider" in captured["command"]
        provider_index = captured["command"].index("--provider") + 1
        assert captured["command"][provider_index] == "openrouter/anthropic/claude-3.5-sonnet"

    def test_approval_token_is_passed_through_env(self, tmp_path, monkeypatch):
        self._set_api_key(monkeypatch)
        captured = {}

        def fake_run(command, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(returncode=0, stdout="final output", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        adapter = HermesAdapter(skills_root=self._skills_root(tmp_path))

        adapter.execute(self._task(), approval_token="approval-123")

        assert captured["env"]["FSH_APPROVAL_TOKEN"] == "approval-123"

    def test_stdout_parser_removes_progress_noise(self, tmp_path, monkeypatch):
        self._set_api_key(monkeypatch)

        def fake_run(command, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="[INFO] booting\nRunning skill\nFinal clean output\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        adapter = HermesAdapter(skills_root=self._skills_root(tmp_path))

        result = adapter.execute(self._task())

        assert result.success is True
        assert result.result_summary == "Final clean output"
