"""
FSH Command Center — Adapter Unit Tests
========================================
Tests for FSHTask validation, pillar defaults, and adapter translate_in.
No external services required.
"""
import sys
from pathlib import Path

# Allow import from fsh-command-center/ without package install
FSH_ROOT = Path(__file__).parent.parent.parent  # repo/fsh-command-center
sys.path.insert(0, str(FSH_ROOT))

import pytest
from adapters.base import (
    FSHTask, FSHTaskResult,
    ApprovalRequiredError, PillarIsolationError,
)
from adapters.abacus_adapter import AbacusAdapter
from adapters.claude_adapter import ClaudeAdapter
from adapters.hermes_adapter import HermesAdapter
from adapters.manus_adapter  import ManusAdapter
from config.pillar_defaults import (
    apply_defaults, get_defaults, validate_compliance_flags, PILLAR_DEFAULTS
)


# ── FSHTask validation ────────────────────────────────────────────────────────

class TestFSHTaskValidation:

    def _base_task(self, **overrides) -> FSHTask:
        defaults = dict(
            schema_version   = "1.0.1",
            task_id          = "00000000-0000-0000-0000-000000000001",
            task_type        = "gridline_daily_review",
            pillar           = "gridline",
            objective        = "Test objective",
            approval_level   = 0,
            execution_engine = "abacus",
        )
        defaults.update(overrides)
        return FSHTask(**defaults)

    def test_valid_task_passes(self):
        task = self._base_task()
        task.validate()  # should not raise

    def test_invalid_task_type_raises(self):
        task = self._base_task(task_type="Invalid Type!")
        with pytest.raises(ValueError, match="task_type"):
            task.validate()

    def test_task_type_with_numbers_valid(self):
        task = self._base_task(task_type="gridline_batch_123")
        task.validate()

    def test_invalid_approval_level(self):
        task = self._base_task(approval_level=5)
        with pytest.raises(ValueError, match="approval_level"):
            task.validate()

    def test_invalid_priority(self):
        task = self._base_task(priority=99)
        with pytest.raises(ValueError, match="priority"):
            task.validate()

    def test_external_action_without_idempotency_key_raises(self):
        task = self._base_task(
            compliance_flags=["external_action"],
            idempotency_key=None
        )
        with pytest.raises(ValueError, match="idempotency_key"):
            task.validate()

    def test_financial_without_idempotency_key_raises(self):
        task = self._base_task(
            compliance_flags=["financial"],
            idempotency_key=None
        )
        with pytest.raises(ValueError, match="idempotency_key"):
            task.validate()

    def test_external_action_with_idempotency_key_passes(self):
        task = self._base_task(
            compliance_flags=["external_action"],
            idempotency_key="idem-key-abc123"
        )
        task.validate()  # should not raise

    def test_rcw_flag_no_idempotency_required(self):
        task = self._base_task(
            compliance_flags=["rcw_18_85", "pii"],
            idempotency_key=None
        )
        task.validate()  # rcw_18_85 and pii don't require idempotency key


# ── Pillar defaults ───────────────────────────────────────────────────────────

class TestPillarDefaults:

    def test_all_six_pillars_defined(self):
        for pillar in ("gridline", "logic", "commerce", "content", "forge", "trading"):
            d = get_defaults(pillar)
            assert d.pillar == pillar

    def test_forge_approval_level_is_2(self):
        """[FIX-CFG-01] Forge must require approval_level=2"""
        d = get_defaults("forge")
        assert d.approval_level == 2, "Forge must have approval_level=2 (was 0 in spec)"

    def test_forge_has_irreversible_flag(self):
        """[FIX-CFG-01] Forge must have irreversible compliance flag"""
        d = get_defaults("forge")
        assert "irreversible" in d.compliance_flags

    def test_trading_approval_level_is_2(self):
        """[FIX-CFG-02] Trading must require approval_level=2"""
        d = get_defaults("trading")
        assert d.approval_level == 2, "Trading must have approval_level=2 (was 1 in spec)"

    def test_commerce_has_affiliate_disclosure(self):
        """[FIX-CFG-03] Commerce must have affiliate_disclosure flag"""
        d = get_defaults("commerce")
        assert "affiliate_disclosure" in d.compliance_flags

    def test_gridline_has_rcw_flag(self):
        d = get_defaults("gridline")
        assert "rcw_18_85" in d.compliance_flags

    def test_trading_postgres_only_storage(self):
        d = get_defaults("trading")
        assert d.storage_targets == ("postgres",), "Trading must be postgres-only"

    def test_apply_defaults_merges_correctly(self):
        payload = apply_defaults({
            "task_type": "gridline_daily_review",
            "pillar":    "gridline",
            "objective": "test",
        })
        assert payload["execution_engine"] == "abacus"
        assert payload["approval_level"]   == 1
        assert "rcw_18_85" in payload["compliance_flags"]
        assert payload["schema_version"]   == "1.0.1"

    def test_apply_defaults_payload_wins(self):
        """Explicit payload values override pillar defaults"""
        payload = apply_defaults({
            "task_type":      "gridline_daily_review",
            "pillar":         "gridline",
            "objective":      "test",
            "approval_level": 0,       # override gridline's default of 1
        })
        assert payload["approval_level"] == 0

    def test_unknown_pillar_raises(self):
        with pytest.raises(ValueError, match="Unknown pillar"):
            get_defaults("nonexistent_pillar")

    def test_validate_compliance_flags_warns_on_missing(self):
        warnings = validate_compliance_flags("forge", [])
        assert any("irreversible" in w for w in warnings)

    def test_validate_compliance_flags_no_warning_when_complete(self):
        warnings = validate_compliance_flags("gridline", ["rcw_18_85", "pii"])
        assert warnings == []


# ── Adapter translate_in ──────────────────────────────────────────────────────

class TestAbacusAdapterTranslateIn:

    def _adapter(self):
        return AbacusAdapter(api_url="http://mock.abacus.ai", api_key="test-key")

    def test_translate_in_gridline(self):
        raw = {
            "task_type": "seller_outreach_draft",
            "pillar":    "gridline",
            "objective": "Draft outreach for 123 Main St",
            "approval_level": 1,
            "idempotency_key": "idem-abc",
            "compliance_flags": ["rcw_18_85", "pii"],
        }
        task = self._adapter().translate_in(raw)
        assert task.pillar    == "gridline"
        assert task.task_type == "seller_outreach_draft"
        assert task.approval_level == 1

    def test_translate_in_rejects_wrong_pillar(self):
        raw = {
            "task_type": "some_task",
            "pillar":    "content",   # not in ABACUS_PILLARS
            "objective": "test",
        }
        with pytest.raises(PillarIsolationError):
            self._adapter().translate_in(raw)

    def test_translate_in_generates_task_id_if_missing(self):
        raw = {
            "task_type": "gridline_daily_review",
            "pillar":    "gridline",
            "objective": "test",
        }
        task = self._adapter().translate_in(raw)
        assert task.task_id is not None and len(task.task_id) == 36

    def test_approval_required_raised_not_blocking(self):
        """P0 fix: AbacusAdapter must raise ApprovalRequiredError, never block"""
        adapter = self._adapter()
        raw = {
            "task_type":       "seller_outreach",
            "pillar":          "gridline",
            "objective":       "Contact seller",
            "approval_level":  1,
            "compliance_flags":["rcw_18_85", "pii"],
            "idempotency_key": "idem-001",
        }
        task = adapter.translate_in(raw)
        # execute() must raise ApprovalRequiredError, not block
        with pytest.raises(ApprovalRequiredError) as exc_info:
            adapter.execute(task, approval_token=None)
        assert exc_info.value.approval_level >= 1
        assert "approval" in exc_info.value.reason.lower()


class TestClaudeAdapterTranslateIn:

    def _adapter(self):
        return ClaudeAdapter(api_key="test-key", model="claude-haiku-3-5-20241022")

    def test_translate_in_logic(self):
        raw = {
            "task_type": "identity_verification",
            "pillar":    "logic",
            "objective": "Verify identity doc",
        }
        task = self._adapter().translate_in(raw)
        assert task.pillar == "logic"

    def test_translate_in_accepts_gridline_analysis(self):
        """Claude handles gridline analysis leg (PLAN/REVIEW phase)"""
        raw = {
            "task_type": "gridline_analysis",
            "pillar":    "gridline",
            "objective": "Analyse lead batch",
        }
        task = self._adapter().translate_in(raw)
        assert task.pillar == "gridline"

    def test_translate_in_rejects_trading(self):
        raw = {
            "task_type": "signal_analysis",
            "pillar":    "trading",
            "objective": "test",
        }
        with pytest.raises(PillarIsolationError):
            self._adapter().translate_in(raw)

    def test_forge_irreversible_without_level2_raises(self):
        """Forge + irreversible flag must require approval_level=2"""
        adapter = self._adapter()
        raw = {
            "task_type":       "sop_publish",
            "pillar":          "forge",
            "objective":       "Publish SOP",
            "approval_level":  1,            # should be 2 for forge+irreversible
            "compliance_flags":["irreversible"],
        }
        task = adapter.translate_in(raw)
        with pytest.raises(ApprovalRequiredError) as exc_info:
            adapter.execute(task)
        assert exc_info.value.approval_level == 2


class TestHermesAdapterTranslateIn:

    def _adapter(self):
        return HermesAdapter(skills_root="skills", hermes_bin="hermes")

    def test_translate_in_content(self):
        raw = {
            "task_type": "content_script_gen",
            "pillar":    "content",
            "objective": "Write a TikTok script",
        }
        task = self._adapter().translate_in(raw)
        assert task.pillar == "content"

    def test_translate_in_rejects_trading(self):
        raw = {
            "task_type": "signal_analysis",
            "pillar":    "trading",
            "objective": "Analyse AAPL signal",
        }
        with pytest.raises(PillarIsolationError):
            self._adapter().translate_in(raw)


class TestManusAdapterTranslateIn:

    def _adapter(self):
        return ManusAdapter(api_url="http://mock.manus.ai", api_key="test-key")

    def test_translate_in_commerce(self):
        raw = {
            "task_type": "product_research",
            "pillar":    "commerce",
            "objective": "Research top Amazon listings",
        }
        task = self._adapter().translate_in(raw)
        assert task.pillar == "commerce"

    def test_translate_in_rejects_forge(self):
        raw = {
            "task_type": "sop_extract",
            "pillar":    "forge",
            "objective": "Extract SOP",
        }
        with pytest.raises(PillarIsolationError):
            self._adapter().translate_in(raw)


# ── Schema JSON validation ────────────────────────────────────────────────────

class TestSchemaFiles:

    def test_schema_v1_0_0_loads(self):
        import json
        schema = json.load(open(
            Path(__file__).parent.parent / "schema" / "task_schema_v1.0.0.json"
        ))
        assert schema.get("$schema") or schema.get("title"), "v1.0.0 must be valid JSON Schema"
        assert "properties" in schema

    def test_schema_v1_0_1_loads(self):
        import json
        schema = json.load(open(
            Path(__file__).parent.parent / "schema" / "task_schema_v1.0.1.json"
        ))
        assert "task_type" in schema["required"], "task_type must be in required"

    def test_schema_v1_0_1_has_idempotency_conditional(self):
        import json
        schema = json.load(open(
            Path(__file__).parent.parent / "schema" / "task_schema_v1.0.1.json"
        ))
        assert "if" in schema, "v1.0.1 must have if/then conditional for idempotency_key"

    def test_schema_v1_0_1_has_priority(self):
        import json
        schema = json.load(open(
            Path(__file__).parent.parent / "schema" / "task_schema_v1.0.1.json"
        ))
        assert "priority" in schema["properties"]
