"""
AETHERIS Gridline Runtime — Skill Registry

Maps skill names to their directories and metadata.
Used by HermesAdapter to locate and execute skills.
"""

from pathlib import Path
from typing import Optional

SKILLS_ROOT = Path(__file__).parent / "skills"

# Skill name → directory mapping
SKILL_REGISTRY = {
    "gridline-lead-ingest": {
        "directory": "gridline-lead-ingest",
        "entry_point": "lead_ingest.py",
        "pillar": "gridline",
        "approval_level": 0,
        "compliance_flags": ["pii"],
    },
    "gridline-lead-scoring": {
        "directory": "gridline-lead-scoring",
        "entry_point": "lead_scoring.py",
        "pillar": "gridline",
        "approval_level": 0,
        "compliance_flags": [],
    },
    "gridline-csv-ingestion": {
        "directory": "gridline-csv-ingestion",
        "entry_point": None,  # SKILL.md only
        "pillar": "gridline",
        "approval_level": 0,
        "compliance_flags": ["pii"],
    },
    "gridline-seller-outreach": {
        "directory": "gridline-seller-outreach",
        "entry_point": None,  # SKILL.md only
        "pillar": "gridline",
        "approval_level": 1,
        "compliance_flags": ["pii", "external_action"],
    },
    "gridline-mao-analysis": {
        "directory": "gridline-mao-analysis",
        "entry_point": "mao_analysis.py",
        "pillar": "gridline",
        "approval_level": 1,
        "compliance_flags": ["financial"],
    },
    "gridline-deal-memo-builder": {
        "directory": "gridline-deal-memo-builder",
        "entry_point": "deal_memo_builder.py",
        "pillar": "gridline",
        "approval_level": 2,
        "compliance_flags": ["financial", "irreversible", "external_action"],
    },
    "gridline-distress-detection": {
        "directory": "gridline-distress-detection",
        "entry_point": None,  # SKILL.md only
        "pillar": "gridline",
        "approval_level": 0,
        "compliance_flags": ["pii"],
    },
    "gridline-property-dedup": {
        "directory": "gridline-property-dedup",
        "entry_point": None,  # SKILL.md only
        "pillar": "gridline",
        "approval_level": 0,
        "compliance_flags": [],
    },
}

# Protected skill prefixes (always require minimum approval)
PROTECTED_PREFIXES = {
    "gridline-deal-memo": 2,
    "gridline-seller-outreach": 1,
    "forge": 1,
}


def locate_skill(skill_name: str) -> Optional[Path]:
    """Find the directory for a skill by name."""
    if skill_name not in SKILL_REGISTRY:
        return None
    skill_dir = SKILLS_ROOT / SKILL_REGISTRY[skill_name]["directory"]
    if skill_dir.exists():
        return skill_dir
    return None


def get_skill_entry_point(skill_name: str) -> Optional[Path]:
    """Find the Python entry point for a skill."""
    if skill_name not in SKILL_REGISTRY:
        return None
    entry = SKILL_REGISTRY[skill_name]["entry_point"]
    if entry is None:
        return None
    skill_dir = locate_skill(skill_name)
    if skill_dir is None:
        return None
    entry_path = skill_dir / entry
    if entry_path.exists():
        return entry_path
    return None


def get_minimum_approval(skill_name: str) -> int:
    """Get the minimum approval level for a skill, considering protected prefixes."""
    # Check protected prefixes first
    for prefix, min_level in PROTECTED_PREFIXES.items():
        if skill_name.startswith(prefix):
            return max(min_level, SKILL_REGISTRY.get(skill_name, {}).get("approval_level", 0))
    # Return skill's own approval level
    return SKILL_REGISTRY.get(skill_name, {}).get("approval_level", 0)
