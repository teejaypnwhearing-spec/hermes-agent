"""
FSH Command Center — Claude Executor
======================================
Decoupled Anthropic SDK wrapper used by ClaudeAdapter.execute().

Design goals:
  - No FSHTask / FSHTaskResult imports — purely a network client
  - Testable without an API key (mock the _client attribute)
  - Extracts structured JSON from three response formats:
      1. Pure JSON string
      2. ```json ... ``` fenced code block
      3. JSON embedded anywhere in prose (last-resort regex scan)
  - Wraps all Anthropic SDK errors as ClaudeExecutorError so the adapter
    has a single exception type to handle

Usage:
    executor = ClaudeExecutor()          # reads ANTHROPIC_API_KEY from env
    result   = executor.run(
        system_prompt = "...",
        user_message  = "...",
        task_id       = "uuid",          # forwarded as X-Task-Id header
    )
    # result.text       → raw response text
    # result.parsed     → dict if JSON was extracted, else None
    # result.success    → True when stop_reason == "end_turn"
    # result.usage      → {"input_tokens": int, "output_tokens": int}
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------
DEFAULT_MODEL      = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 4096
_ENV_MODEL_KEY     = "FSH_CLAUDE_MODEL"
_ENV_API_KEY       = "ANTHROPIC_API_KEY"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class ClaudeExecutorResult:
    text:    str
    success: bool
    usage:   dict[str, int] = field(default_factory=dict)
    parsed:  dict[str, Any] | None = None
    model:   str = ""


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------
class ClaudeExecutorError(Exception):
    """
    Wraps all Anthropic SDK errors so callers handle one exception type.

    Attributes
    ----------
    error_class : str
        One of: "timeout", "rate_limit", "api_error", "connection_error"
    status_code : int | None
        HTTP status code when available.
    """
    def __init__(
        self,
        message:     str,
        error_class: str,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.error_class = error_class
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------
class ClaudeExecutor:
    """
    Thin wrapper around the Anthropic Messages API.

    Parameters
    ----------
    api_key   : Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
    model     : Model ID. Falls back to FSH_CLAUDE_MODEL env var, then DEFAULT_MODEL.
    max_tokens: Maximum tokens in the response.
    """

    def __init__(
        self,
        api_key:    str | None = None,
        model:      str | None = None,
        max_tokens: int        = DEFAULT_MAX_TOKENS,
    ):
        resolved_key = api_key or os.environ.get(_ENV_API_KEY, "")
        self._client    = anthropic.Anthropic(api_key=resolved_key)
        self.model      = model or os.environ.get(_ENV_MODEL_KEY, DEFAULT_MODEL)
        self.max_tokens = max_tokens

    # ── Public API ──────────────────────────────────────────────────────────

    def run(
        self,
        system_prompt: str,
        user_message:  str,
        task_id:       str | None = None,
    ) -> ClaudeExecutorResult:
        """
        Call the Anthropic Messages API and return a ClaudeExecutorResult.

        Raises ClaudeExecutorError on any API failure.
        """
        extra_headers: dict[str, str] = {}
        if task_id:
            extra_headers["X-Task-Id"] = task_id

        try:
            response = self._client.messages.create(
                model          = self.model,
                max_tokens     = self.max_tokens,
                system         = system_prompt,
                messages       = [{"role": "user", "content": user_message}],
                extra_headers  = extra_headers or None,
            )
        except anthropic.APITimeoutError as exc:
            raise ClaudeExecutorError(
                f"Claude API timed out: {exc}",
                error_class = "timeout",
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ClaudeExecutorError(
                f"Claude rate limit exceeded: {exc}",
                error_class = "rate_limit",
                status_code = 429,
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ClaudeExecutorError(
                f"Claude API error {exc.status_code}: {exc.message}",
                error_class = "api_error",
                status_code = exc.status_code,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeExecutorError(
                f"Claude connection error: {exc}",
                error_class = "connection_error",
            ) from exc

        raw_text = response.content[0].text if response.content else ""
        parsed   = self._extract_json(raw_text)

        return ClaudeExecutorResult(
            text    = raw_text,
            success = response.stop_reason == "end_turn",
            usage   = {
                "input_tokens":  response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            parsed  = parsed,
            model   = response.model,
        )

    # ── JSON extraction ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """
        Try three strategies to extract a JSON dict from Claude's response.

        1. The entire text is a JSON object.
        2. The text contains a ```json ... ``` fenced block.
        3. Scan for the first {...} balanced block in the text.

        Returns None if no valid JSON dict is found.
        """
        if not text:
            return None

        # Strategy 1 — pure JSON
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        # Strategy 2 — fenced code block
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1))
            except json.JSONDecodeError:
                pass

        # Strategy 3 — first balanced { ... } in prose
        start = text.find("{")
        if start != -1:
            depth = 0
            for i, ch in enumerate(text[start:], start=start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break

        return None
