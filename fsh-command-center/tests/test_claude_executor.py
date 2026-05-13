"""
FSH Command Center — Claude Executor Tests
==========================================
Unit tests for ClaudeExecutor and ClaudeAdapter.execute().

No external services required — all Anthropic API calls are mocked.
Run with:
    python -m pytest fsh-command-center/tests/test_claude_executor.py -v --override-ini="addopts="
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow import from fsh-command-center/ without package install
FSH_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(FSH_ROOT))

from adapters.claude_executor import (
    ClaudeExecutor,
    ClaudeExecutorError,
    ClaudeExecutorResult,
    DEFAULT_MODEL,
)
from adapters.claude_adapter import ClaudeAdapter
from adapters.base import FSHTask, ApprovalRequiredError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(text: str, stop_reason: str = "end_turn") -> MagicMock:
    """Build a minimal mock of an Anthropic Messages response."""
    content_block = MagicMock()
    content_block.text = text

    usage = MagicMock()
    usage.input_tokens  = 100
    usage.output_tokens = 50

    resp = MagicMock()
    resp.content     = [content_block]
    resp.stop_reason = stop_reason
    resp.usage       = usage
    resp.model       = DEFAULT_MODEL
    return resp


def _base_task(**overrides) -> FSHTask:
    defaults = dict(
        schema_version   = "1.0.1",
        task_id          = "aaaaaaaa-0000-0000-0000-000000000001",
        task_type        = "logic_analysis",
        pillar           = "logic",
        objective        = "Analyse the workflow",
        approval_level   = 0,
        execution_engine = "claude",
    )
    defaults.update(overrides)
    return FSHTask(**defaults)


# ---------------------------------------------------------------------------
# ClaudeExecutor — JSON extraction
# ---------------------------------------------------------------------------

class TestClaudeExecutorJsonExtraction:
    def test_pure_json_object(self):
        text = '{"plan_steps": ["step1"], "reasoning": "ok"}'
        result = ClaudeExecutor._extract_json(text)
        assert result == {"plan_steps": ["step1"], "reasoning": "ok"}

    def test_fenced_json_block(self):
        text = 'Here is the plan:\n```json\n{"plan_steps": ["a", "b"]}\n```\nDone.'
        result = ClaudeExecutor._extract_json(text)
        assert result["plan_steps"] == ["a", "b"]

    def test_fenced_block_no_language_tag(self):
        text = "```\n{\"key\": \"value\"}\n```"
        result = ClaudeExecutor._extract_json(text)
        assert result == {"key": "value"}

    def test_json_embedded_in_prose(self):
        text = 'Analysis complete. Result: {"status": "ok", "score": 42} — end.'
        result = ClaudeExecutor._extract_json(text)
        assert result["status"] == "ok"
        assert result["score"] == 42

    def test_no_json_returns_none(self):
        result = ClaudeExecutor._extract_json("No JSON here at all.")
        assert result is None

    def test_empty_string_returns_none(self):
        result = ClaudeExecutor._extract_json("")
        assert result is None

    def test_malformed_json_returns_none(self):
        result = ClaudeExecutor._extract_json("{bad json: no quotes}")
        assert result is None

    def test_nested_json_extracted(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = ClaudeExecutor._extract_json(text)
        assert result["outer"]["inner"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# ClaudeExecutor — run() with mocked Anthropic client
# ---------------------------------------------------------------------------

class TestClaudeExecutorRun:
    def _executor(self, mock_client: MagicMock) -> ClaudeExecutor:
        executor = ClaudeExecutor(api_key="test-key")
        executor._client = mock_client
        return executor

    def test_successful_run_returns_result(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(
            '{"plan_steps": ["step1"], "reasoning": "test"}'
        )
        executor = self._executor(mock_client)
        result = executor.run("system", "user", task_id="task-123")

        assert isinstance(result, ClaudeExecutorResult)
        assert result.success is True
        assert result.parsed == {"plan_steps": ["step1"], "reasoning": "test"}
        assert result.usage["input_tokens"] == 100

    def test_non_end_turn_stop_reason_is_not_success(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(
            "truncated response", stop_reason="max_tokens"
        )
        executor = self._executor(mock_client)
        result = executor.run("system", "user")
        assert result.success is False

    def test_timeout_raises_executor_error(self):
        import anthropic
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())
        executor = self._executor(mock_client)

        with pytest.raises(ClaudeExecutorError) as exc_info:
            executor.run("system", "user")
        assert exc_info.value.error_class == "timeout"

    def test_rate_limit_raises_executor_error(self):
        import anthropic
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limited", response=MagicMock(status_code=429), body={}
        )
        executor = self._executor(mock_client)

        with pytest.raises(ClaudeExecutorError) as exc_info:
            executor.run("system", "user")
        assert exc_info.value.error_class == "rate_limit"
        assert exc_info.value.status_code == 429

    def test_api_status_error_raises_executor_error(self):
        import anthropic
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.messages.create.side_effect = anthropic.APIStatusError(
            message="server error", response=mock_response, body={}
        )
        executor = self._executor(mock_client)

        with pytest.raises(ClaudeExecutorError) as exc_info:
            executor.run("system", "user")
        assert exc_info.value.error_class == "api_error"
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# ClaudeAdapter.execute() with injected mock executor
# ---------------------------------------------------------------------------

class TestClaudeAdapterExecute:
    def _mock_executor(self, text: str, success: bool = True) -> MagicMock:
        mock = MagicMock(spec=ClaudeExecutor)
        mock.model = DEFAULT_MODEL
        mock.run.return_value = ClaudeExecutorResult(
            text    = text,
            success = success,
            usage   = {"input_tokens": 80, "output_tokens": 40},
            parsed  = ClaudeExecutor._extract_json(text),
            model   = DEFAULT_MODEL,
        )
        return mock

    def test_execute_returns_task_result(self):
        executor = self._mock_executor('{"plan_steps": ["a"], "reasoning": "ok"}')
        adapter  = ClaudeAdapter(executor=executor)
        task     = _base_task()
        result   = adapter.execute(task)

        assert result.success is True
        assert result.task_id == task.task_id
        assert len(result.artifacts) == 1
        assert result.artifacts[0]["label"] == "claude_response"

    def test_execute_on_executor_error_returns_failure(self):
        mock = MagicMock(spec=ClaudeExecutor)
        mock.model = DEFAULT_MODEL
        mock.run.side_effect = ClaudeExecutorError("timeout", error_class="timeout")
        adapter = ClaudeAdapter(executor=mock)
        task    = _base_task()
        result  = adapter.execute(task)

        assert result.success is False
        assert result.error_detail["error_class"] == "timeout"

    def test_forge_irreversible_without_level2_raises(self):
        executor = self._mock_executor("{}")
        adapter  = ClaudeAdapter(executor=executor)
        task     = _base_task(
            pillar           = "forge",
            task_type        = "sop_extract",
            compliance_flags = ["irreversible"],
            approval_level   = 1,
        )
        with pytest.raises(ApprovalRequiredError) as exc_info:
            adapter.execute(task)
        assert exc_info.value.approval_level == 2

    def test_translate_out_extracts_structured_json(self):
        executor = self._mock_executor('{"plan_steps": ["x"], "risks": []}')
        adapter  = ClaudeAdapter(executor=executor)
        task     = _base_task()
        fsh_result = adapter.execute(task)
        out = adapter.translate_out(fsh_result)

        assert out["success"] is True
        assert out["structured"]["plan_steps"] == ["x"]
