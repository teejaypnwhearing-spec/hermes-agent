"""
FSH Command Center — HermesAdapter P0 Fix Tests
================================================
Verifies that HermesAdapter calls `hermes-agent --query` (not `hermes run`)
and that all error conditions are handled correctly.

P0 Fix: fix/hermes-adapter-p0
  - hermes run → hermes-agent --query
  - hermes_bin default: "hermes" → "hermes-agent"

No external services required — subprocess.run is fully mocked.
"""
import json
import sys
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Allow import from fsh-command-center/ without package install
FSH_ROOT = Path(__file__).parent.parent.parent   # repo root
sys.path.insert(0, str(FSH_ROOT))

from adapters.hermes_adapter import HermesAdapter, HERMES_PILLARS
from adapters.base import (
    FSHTask,
    FSHTaskResult,
    ApprovalRequiredError,
    PillarIsolationError,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_task(**overrides) -> FSHTask:
    """Return a minimal valid FSHTask targeting the Hermes adapter."""
    defaults = dict(
        schema_version   = "1.0.1",
        task_id          = "test-task-id-0001",
        task_type        = "gridline_lead_ingest",
        pillar           = "gridline",
        objective        = "Ingest today's lead batch from Investra",
        approval_level   = 0,
        execution_engine = "hermes",
    )
    defaults.update(overrides)
    return FSHTask(**defaults)


def _make_adapter(**kwargs) -> HermesAdapter:
    """Return a HermesAdapter with a test skills_root."""
    return HermesAdapter(
        skills_root=str(FSH_ROOT / "fsh-command-center" / "skills"),
        **kwargs,
    )


def _mock_result(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    """Build a mock subprocess.CompletedProcess."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout     = stdout
    m.stderr     = stderr
    m.returncode = returncode
    return m


# ── P0 Core: correct binary and command structure ─────────────────────────────

class TestHermesAgentCommandStructure:
    """
    P0 guarantee: `hermes-agent --query` is called — never `hermes run`.
    """

    def test_default_binary_is_hermes_agent(self):
        """hermes_bin default must be 'hermes-agent', not 'hermes'."""
        adapter = _make_adapter()
        assert adapter.hermes_bin == "hermes-agent", (
            "P0 FIX: default hermes_bin must be 'hermes-agent'"
        )

    def test_execute_calls_hermes_agent_not_hermes_run(self):
        """subprocess.run must receive 'hermes-agent' as argv[0], not 'hermes run'."""
        adapter = _make_adapter()
        task    = _make_task()
        mock_ok = _mock_result(stdout=json.dumps({"output": "done"}))

        with patch("subprocess.run", return_value=mock_ok) as mock_run:
            adapter.execute(task)

        assert mock_run.called
        cmd = mock_run.call_args[0][0]          # positional first arg = cmd list

        # Must NOT be the old broken invocation
        assert "run" not in cmd, (
            f"P0 BUG: 'run' found in command — was using hermes run. cmd={cmd}"
        )
        # Must be the correct binary
        assert cmd[0] == "hermes-agent", (
            f"P0 BUG: binary is '{cmd[0]}' not 'hermes-agent'. cmd={cmd}"
        )

    def test_execute_uses_query_flag(self):
        """Command must include --query flag."""
        adapter = _make_adapter()
        task    = _make_task()
        mock_ok = _mock_result(stdout=json.dumps({"output": "done"}))

        with patch("subprocess.run", return_value=mock_ok) as mock_run:
            adapter.execute(task)

        cmd = mock_run.call_args[0][0]
        assert "--query" in cmd, f"--query flag missing from cmd: {cmd}"

    def test_execute_does_not_use_input_flag(self):
        """Must NOT use deprecated --input flag (old hermes run interface)."""
        adapter = _make_adapter()
        task    = _make_task()
        mock_ok = _mock_result(stdout=json.dumps({"output": "done"}))

        with patch("subprocess.run", return_value=mock_ok) as mock_run:
            adapter.execute(task)

        cmd = mock_run.call_args[0][0]
        assert "--input" not in cmd, (
            f"Deprecated --input flag still present in cmd: {cmd}"
        )


# ── P0 Parameters: all 5 required args present ───────────────────────────────

class TestHermesAgentParameterPassing:
    """
    All 5 required parameters must be passed to hermes-agent:
    --query, --context, --output-format, --timeout, --skill
    """

    def _run_and_get_cmd(self, task: FSHTask, **execute_kwargs) -> list[str]:
        adapter = _make_adapter()
        mock_ok = _mock_result(stdout=json.dumps({"output": "ok"}))
        with patch("subprocess.run", return_value=mock_ok) as mock_run:
            adapter.execute(task, **execute_kwargs)
        return mock_run.call_args[0][0]

    def test_query_param_contains_objective(self):
        task = _make_task(objective="Ingest leads from Investra batch 2025-01")
        cmd  = self._run_and_get_cmd(task)
        idx  = cmd.index("--query")
        assert "Ingest leads from Investra batch 2025-01" in cmd[idx + 1]

    def test_context_param_is_valid_json(self):
        task = _make_task()
        cmd  = self._run_and_get_cmd(task)
        idx  = cmd.index("--context")
        ctx  = json.loads(cmd[idx + 1])            # must not raise
        assert isinstance(ctx, dict)
        assert "task_id" in ctx
        assert "pillar"  in ctx

    def test_context_contains_task_id(self):
        task = _make_task(task_id="task-uuid-abc")
        cmd  = self._run_and_get_cmd(task)
        idx  = cmd.index("--context")
        ctx  = json.loads(cmd[idx + 1])
        assert ctx["task_id"] == "task-uuid-abc"

    def test_output_format_is_json(self):
        task = _make_task()
        cmd  = self._run_and_get_cmd(task)
        idx  = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"

    def test_timeout_param_is_present(self):
        task = _make_task()
        cmd  = self._run_and_get_cmd(task)
        assert "--timeout" in cmd

    def test_timeout_param_value_matches(self):
        task = _make_task()
        cmd  = self._run_and_get_cmd(task, timeout=120)
        idx  = cmd.index("--timeout")
        assert cmd[idx + 1] == "120"

    def test_skill_param_derived_from_task_type(self):
        """task_type 'gridline_lead_ingest' → skill 'gridline-lead-ingest'."""
        task = _make_task(task_type="gridline_lead_ingest")
        cmd  = self._run_and_get_cmd(task)
        idx  = cmd.index("--skill")
        assert cmd[idx + 1] == "gridline-lead-ingest"

    def test_all_five_params_present(self):
        """All five required params must be present in a single execute call."""
        task     = _make_task()
        cmd      = self._run_and_get_cmd(task)
        required = {"--query", "--context", "--output-format", "--timeout", "--skill"}
        missing  = required - set(cmd)
        assert not missing, f"Missing required params: {missing}"


# ── Error handling ─────────────────────────────────────────────────────────────

class TestHermesAgentErrorHandling:
    """
    All error conditions must produce FSHTaskResult(success=False) with
    structured error_detail. No exceptions should propagate to callers.
    """

    def _adapter(self) -> HermesAdapter:
        return _make_adapter()

    # --- Binary not found ---

    def test_binary_not_found_returns_failed_result(self):
        adapter = self._adapter()
        task    = _make_task()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = adapter.execute(task)
        assert result.success is False
        assert result.error_detail["error_class"] == "binary_not_found"

    def test_binary_not_found_message_includes_installation_hint(self):
        adapter = self._adapter()
        task    = _make_task()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = adapter.execute(task)
        summary = result.result_summary.lower()
        assert any(kw in summary for kw in ("not found", "install", "path")), (
            f"Error message should include installation guidance. Got: {result.result_summary}"
        )

    # --- Non-zero exit code ---

    def test_nonzero_exit_returns_failed_result(self):
        adapter  = self._adapter()
        task     = _make_task()
        mock_err = _mock_result(returncode=1, stderr="skill module not found")
        with patch("subprocess.run", return_value=mock_err):
            result = adapter.execute(task)
        assert result.success is False
        assert result.error_detail["error_class"] == "execution_error"

    def test_nonzero_exit_includes_exit_code_in_detail(self):
        adapter  = self._adapter()
        task     = _make_task()
        mock_err = _mock_result(returncode=2, stderr="configuration error")
        with patch("subprocess.run", return_value=mock_err):
            result = adapter.execute(task)
        assert "2" in result.result_summary or "2" in str(result.error_detail)

    def test_nonzero_exit_includes_stderr_in_detail(self):
        adapter  = self._adapter()
        task     = _make_task()
        mock_err = _mock_result(returncode=1, stderr="CRITICAL: missing ANTHROPIC_API_KEY")
        with patch("subprocess.run", return_value=mock_err):
            result = adapter.execute(task)
        # stderr must be surfaced somewhere accessible
        combined = result.result_summary + json.dumps(result.error_detail or {})
        assert "ANTHROPIC_API_KEY" in combined or "CRITICAL" in combined

    # --- Timeout ---

    def test_timeout_returns_failed_result(self):
        adapter = self._adapter()
        task    = _make_task()
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="hermes-agent", timeout=300)):
            result = adapter.execute(task)
        assert result.success is False
        assert result.error_detail["error_class"] == "timeout"

    def test_timeout_message_includes_skill_name(self):
        adapter = self._adapter()
        task    = _make_task(task_type="gridline_lead_ingest")
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="hermes-agent", timeout=300)):
            result = adapter.execute(task)
        assert "gridline-lead-ingest" in result.result_summary

    def test_timeout_message_includes_timeout_value(self):
        adapter = self._adapter()
        task    = _make_task()
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="hermes-agent", timeout=120)):
            result = adapter.execute(task, timeout=120)
        assert "120" in result.result_summary

    # --- Non-JSON response ---

    def test_non_json_response_returns_failed_result(self):
        adapter      = self._adapter()
        task         = _make_task()
        mock_garbage = _mock_result(stdout="not valid json!!!", returncode=0)
        with patch("subprocess.run", return_value=mock_garbage):
            result = adapter.execute(task)
        assert result.success is False

    def test_non_json_includes_first_500_chars_of_stdout(self):
        adapter     = self._adapter()
        task        = _make_task()
        bad_output  = "ERROR: " + ("x" * 600)
        mock_bad    = _mock_result(stdout=bad_output, returncode=0)
        with patch("subprocess.run", return_value=mock_bad):
            result = adapter.execute(task)
        # Error detail must reference truncated stdout
        combined = result.result_summary + json.dumps(result.error_detail or {})
        assert "ERROR:" in combined

    def test_empty_stdout_returns_failed_result(self):
        adapter    = self._adapter()
        task       = _make_task()
        mock_empty = _mock_result(stdout="", returncode=0)
        with patch("subprocess.run", return_value=mock_empty):
            result = adapter.execute(task)
        assert result.success is False

    # --- Missing/invalid parameters ---

    def test_empty_query_raises_before_subprocess(self):
        adapter = self._adapter()
        with patch("subprocess.run") as mock_run:
            with pytest.raises(RuntimeError, match="non-empty --query"):
                adapter._call_hermes_agent(
                    query="",
                    context={},
                    skill="gridline-lead-ingest",
                )
        mock_run.assert_not_called()

    def test_empty_skill_raises_before_subprocess(self):
        adapter = self._adapter()
        with patch("subprocess.run") as mock_run:
            with pytest.raises(RuntimeError, match="non-empty --skill"):
                adapter._call_hermes_agent(
                    query="Do something",
                    context={},
                    skill="",
                )
        mock_run.assert_not_called()


# ── Subprocess call correctness ───────────────────────────────────────────────

class TestSubprocessRunConfiguration:
    """
    Verify subprocess.run is called with the correct kwargs.
    """

    def _execute_and_capture(self, task: FSHTask) -> MagicMock:
        adapter = _make_adapter()
        mock_ok = _mock_result(stdout=json.dumps({"output": "success"}))
        with patch("subprocess.run", return_value=mock_ok) as mock_run:
            adapter.execute(task)
        return mock_run

    def test_capture_output_true(self):
        task     = _make_task()
        mock_run = self._execute_and_capture(task)
        kwargs   = mock_run.call_args[1]
        assert kwargs.get("capture_output") is True

    def test_text_mode_true(self):
        task     = _make_task()
        mock_run = self._execute_and_capture(task)
        kwargs   = mock_run.call_args[1]
        assert kwargs.get("text") is True

    def test_check_is_false(self):
        """check=False required — we handle returncode manually."""
        task     = _make_task()
        mock_run = self._execute_and_capture(task)
        kwargs   = mock_run.call_args[1]
        assert kwargs.get("check") is False

    def test_timeout_has_buffer(self):
        """subprocess timeout must be > hermes timeout (buffer against kill race)."""
        task    = _make_task()
        adapter = _make_adapter()
        mock_ok = _mock_result(stdout=json.dumps({"output": "ok"}))
        with patch("subprocess.run", return_value=mock_ok) as mock_run:
            adapter.execute(task, timeout=120)
        kwargs = mock_run.call_args[1]
        assert kwargs["timeout"] > 120, (
            "subprocess timeout must exceed hermes --timeout to allow graceful exit"
        )

    def test_env_contains_fsh_task_id(self):
        """FSH_TASK_ID must be injected into subprocess env."""
        task    = _make_task(task_id="env-test-uuid-001")
        adapter = _make_adapter()
        mock_ok = _mock_result(stdout=json.dumps({"output": "ok"}))
        with patch("subprocess.run", return_value=mock_ok) as mock_run:
            adapter.execute(task)
        env = mock_run.call_args[1].get("env", {})
        assert env.get("FSH_TASK_ID") == "env-test-uuid-001"


# ── Approval gate (unchanged) ─────────────────────────────────────────────────

class TestHermesAdapterApprovalGate:
    """Approval gate must still fire for protected skills."""

    def test_protected_skill_raises_approval_required(self):
        adapter = _make_adapter()
        task    = _make_task(
            task_type        = "gridline_seller_outreach",
            approval_level   = 1,
            compliance_flags = ["rcw_18_85", "external_action"],
            idempotency_key  = "idem-test-001",
        )
        with patch("subprocess.run"):
            with pytest.raises(ApprovalRequiredError) as exc_info:
                adapter.execute(task)
        assert exc_info.value.approval_level >= 1

    def test_approval_gate_fires_before_subprocess(self):
        """subprocess.run must never be called for protected skills awaiting approval."""
        adapter = _make_adapter()
        task    = _make_task(
            task_type        = "gridline_seller_outreach",
            approval_level   = 1,
            compliance_flags = ["rcw_18_85", "external_action"],
            idempotency_key  = "idem-test-002",
        )
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ApprovalRequiredError):
                adapter.execute(task)
        mock_run.assert_not_called()


# ── Happy path ────────────────────────────────────────────────────────────────

class TestHermesAdapterHappyPath:
    """Successful execution returns FSHTaskResult(success=True)."""

    def test_successful_json_response_returns_success(self):
        adapter = _make_adapter()
        task    = _make_task()
        payload = {"output": "Ingested 47 leads. Score range: 42–91.", "count": 47}
        mock_ok = _mock_result(stdout=json.dumps(payload), returncode=0)

        with patch("subprocess.run", return_value=mock_ok):
            result = adapter.execute(task)

        assert result.success is True
        assert result.task_id == task.task_id

    def test_result_summary_truncated_at_2000_chars(self):
        adapter     = _make_adapter()
        task        = _make_task()
        long_output = {"output": "A" * 3000}
        mock_ok     = _mock_result(stdout=json.dumps(long_output), returncode=0)

        with patch("subprocess.run", return_value=mock_ok):
            result = adapter.execute(task)

        assert len(result.result_summary) <= 2000

    def test_custom_hermes_bin_passed_to_subprocess(self):
        """Custom binary path must be forwarded to subprocess."""
        adapter = HermesAdapter(hermes_bin="/usr/local/bin/hermes-agent")
        task    = _make_task()
        mock_ok = _mock_result(stdout=json.dumps({"output": "ok"}))

        with patch("subprocess.run", return_value=mock_ok) as mock_run:
            adapter.execute(task)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/hermes-agent"


# ── Pillar isolation (unchanged) ──────────────────────────────────────────────

class TestHermesAdapterPillarIsolation:

    def test_allowed_pillars(self):
        adapter = _make_adapter()
        for pillar in ("content", "gridline", "forge"):
            raw = {
                "task_type": f"{pillar}_test_task",
                "pillar":    pillar,
                "objective": f"Test {pillar}",
            }
            task = adapter.translate_in(raw)
            assert task.pillar == pillar

    def test_trading_rejected(self):
        adapter = _make_adapter()
        raw = {"task_type": "signal_analysis", "pillar": "trading", "objective": "test"}
        with pytest.raises(PillarIsolationError):
            adapter.translate_in(raw)

    def test_logic_rejected(self):
        adapter = _make_adapter()
        raw = {"task_type": "identity_check", "pillar": "logic", "objective": "test"}
        with pytest.raises(PillarIsolationError):
            adapter.translate_in(raw)


# ── Skill name resolution ─────────────────────────────────────────────────────

class TestSkillResolution:

    def test_snake_to_kebab_conversion(self):
        adapter = _make_adapter()
        assert adapter._resolve_skill("gridline_lead_ingest")    == "gridline-lead-ingest"
        assert adapter._resolve_skill("gridline_seller_outreach") == "gridline-seller-outreach"
        assert adapter._resolve_skill("content_script_gen")       == "content-script-gen"
        assert adapter._resolve_skill("forge_sop_publish")        == "forge-sop-publish"
