"""
FSH Command Center — Pillar Defaults Configuration
===================================================
Canonical per-pillar defaults for execution_engine, compliance_flags,
approval_level, and storage_targets.

Review-ref: fsh_architecture_review.md §SCHEMA & CONTRACT REVIEW
  [FIX-CFG-01] Forge compliance_flags now inherit 'irreversible' by default
               (spec had empty [] — dangerously permissive)
  [FIX-CFG-02] Trading default approval_level raised to 2
               (financial + external_action requires highest gate)
  [FIX-CFG-03] Commerce gains 'affiliate_disclosure' flag
               (Amazon/TikTok affiliate content disclosure requirement)

Usage:
    from fsh_command_center.config.pillar_defaults import get_defaults, apply_defaults
    defaults = get_defaults("gridline")
    task = apply_defaults(raw_task_payload, pillar="gridline")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Per-pillar default dataclass ──────────────────────────────────────────────

@dataclass(frozen=True)
class PillarDefaults:
    pillar:             str
    execution_engine:   str
    approval_level:     int
    compliance_flags:   tuple[str, ...]
    storage_targets:    tuple[str, ...]
    notes:              str = ""


# ── Registry ──────────────────────────────────────────────────────────────────
#
# IMPORTANT — override order when merging with task payload:
#   1. PillarDefaults (baseline)
#   2. task payload fields (explicit overrides)
#   3. Schema conditional validation (idempotency_key enforcement)
#
# Changing approval_level below requires updating pillar_defaults table in DB.

PILLAR_DEFAULTS: dict[str, PillarDefaults] = {

    "gridline": PillarDefaults(
        pillar           = "gridline",
        execution_engine = "abacus",
        approval_level   = 1,        # all seller outreach needs human sign-off
        compliance_flags = ("rcw_18_85", "pii"),
        storage_targets  = ("postgres", "notion", "git"),
        notes            = (
            "RCW 18.85 governs all seller outreach in WA. "
            "rcw_status must reach 'compliant' before any outreach attempt. "
            "PII stored in postgres only; Notion receives non-PII summaries."
        ),
    ),

    "logic": PillarDefaults(
        pillar           = "logic",
        execution_engine = "claude",
        approval_level   = 0,
        compliance_flags = ("pii",),
        storage_targets  = ("postgres", "notion"),
        notes            = (
            "Digital identity and credential workflows. "
            "PII flag ensures all data handling is logged and access-controlled."
        ),
    ),

    "commerce": PillarDefaults(
        pillar           = "commerce",
        execution_engine = "abacus",
        approval_level   = 1,
        # [FIX-CFG-03] affiliate_disclosure added — Amazon/TikTok shop legal requirement
        compliance_flags = ("external_action", "affiliate_disclosure"),
        storage_targets  = ("postgres", "notion"),
        notes            = (
            "Amazon, TikTok Shop, Shopify integrations. "
            "affiliate_disclosure required for all promotional content. "
            "external_action flag mandates idempotency_key on all order/listing tasks."
        ),
    ),

    "content": PillarDefaults(
        pillar           = "content",
        execution_engine = "hermes",
        approval_level   = 0,
        compliance_flags = (),        # no regulatory flags by default
        storage_targets  = ("postgres", "git"),
        notes            = (
            "Content generation pipeline (scripts, blogs, social). "
            "Git storage enables version history for published content. "
            "Tasks touching affiliate content should override compliance_flags."
        ),
    ),

    "forge": PillarDefaults(
        pillar           = "forge",
        execution_engine = "claude",
        # [FIX-CFG-01] raised from 0 to 2 — IP/SOP extraction is irreversible
        approval_level   = 2,
        # [FIX-CFG-01] irreversible flag added — was [] in spec
        compliance_flags = ("irreversible",),
        storage_targets  = ("postgres", "git"),
        notes            = (
            "CRITICAL: Forge creates/publishes IP assets and SOPs. "
            "approval_level=2 required for all tasks (up from spec baseline of 0). "
            "'irreversible' flag ensures idempotency_key is mandatory on publish tasks. "
            "Spec had approval_level=0 and compliance_flags=[] — both were wrong."
        ),
    ),

    "trading": PillarDefaults(
        pillar           = "trading",
        execution_engine = "abacus",
        # [FIX-CFG-02] raised from 1 to 2 — financial + external = maximum gate
        approval_level   = 2,
        compliance_flags = ("financial", "external_action"),
        storage_targets  = ("postgres",),   # trading data stays in postgres only
        notes            = (
            "Trading signals, capital deployment. "
            "approval_level=2 (maximum) for all tasks. "
            "financial + external_action flags mandate idempotency_key on every task. "
            "Storage is postgres-only; no Notion/Git for raw financial data."
        ),
    ),
}


# ── Accessors ─────────────────────────────────────────────────────────────────

def get_defaults(pillar: str) -> PillarDefaults:
    """Return PillarDefaults for the given pillar name."""
    try:
        return PILLAR_DEFAULTS[pillar.lower()]
    except KeyError:
        valid = list(PILLAR_DEFAULTS.keys())
        raise ValueError(f"Unknown pillar '{pillar}'. Valid pillars: {valid}") from None


def apply_defaults(raw_payload: dict[str, Any], pillar: str | None = None) -> dict[str, Any]:
    """
    Merge pillar defaults into a raw task payload.

    Fields already present in raw_payload are NOT overwritten (payload wins).
    This allows individual tasks to override defaults intentionally.

    Parameters
    ----------
    raw_payload : incoming task dict (may be partial)
    pillar      : override pillar name; falls back to raw_payload["pillar"]

    Returns
    -------
    Merged dict suitable for passing to an adapter's translate_in()
    """
    pillar_name = pillar or raw_payload.get("pillar")
    if not pillar_name:
        raise ValueError("pillar must be provided in raw_payload or as a keyword argument")

    defaults = get_defaults(pillar_name)
    merged: dict[str, Any] = {
        "schema_version":    "1.0.1",
        "execution_engine":  defaults.execution_engine,
        "approval_level":    defaults.approval_level,
        "compliance_flags":  list(defaults.compliance_flags),
        "storage_targets":   list(defaults.storage_targets),
        "priority":          2,   # standard
        "context_artifacts": [],
        "retry_policy": {
            "max_attempts":     3,
            "backoff_strategy": "exponential",
            "retry_on":         ["timeout", "rate_limit"],
        },
    }

    # Raw payload values override defaults
    merged.update(raw_payload)

    # Always ensure pillar is set to the resolved value
    merged["pillar"] = pillar_name

    return merged


# ── Validation helper ─────────────────────────────────────────────────────────

def validate_compliance_flags(pillar: str, flags: list[str]) -> list[str]:
    """
    Return a list of warning strings for any compliance_flags that are
    weaker than the pillar's defaults.

    Useful for pre-flight checks before submitting a task.
    """
    defaults = get_defaults(pillar)
    warnings = []
    for required_flag in defaults.compliance_flags:
        if required_flag not in flags:
            warnings.append(
                f"WARNING: pillar='{pillar}' default includes '{required_flag}' "
                f"but it is absent from this task's compliance_flags. "
                f"Ensure this is intentional."
            )
    return warnings
