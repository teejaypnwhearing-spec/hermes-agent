"""
AETHERIS Gridline Runtime — Utility Functions

Shared utilities used across AETHERIS skills and modules.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any


def generate_uuid() -> str:
    """Generate a new UUID v4 string."""
    return str(uuid.uuid4())


def utc_now() -> str:
    """Get current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def safe_json_loads(data: str) -> Any:
    """Safely parse JSON, returning None on failure."""
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None


def truncate(text: str, max_length: int = 2000) -> str:
    """Truncate text to max_length with ellipsis indicator."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def normalize_address(address: str) -> str:
    """Normalize a property address for deduplication comparison."""
    return " ".join(address.strip().lower().split())


def tier_from_score(score: float) -> str:
    """Determine tier from lead score."""
    if score >= 75:
        return "A"
    elif score >= 50:
        return "B"
    elif score >= 25:
        return "C"
    else:
        return "D"
