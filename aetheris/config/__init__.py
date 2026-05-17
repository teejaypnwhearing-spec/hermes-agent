"""
AETHERIS Gridline Runtime — Configuration Module

Loads configuration from environment variables with sensible defaults.
Never hardcodes secrets — all sensitive values come from .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")


class AetherisConfig:
    """Runtime configuration for the AETHERIS Gridline system."""

    # Database
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "fsh_command")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")

    # Hermes Agent
    HERMES_BIN: str = os.getenv("HERMES_BIN", "hermes-agent")
    HERMES_MAX_TURNS: int = int(os.getenv("HERMES_MAX_TURNS", "5"))
    HERMES_TIMEOUT: int = int(os.getenv("HERMES_TIMEOUT", "300"))

    # n8n
    N8N_WEBHOOK_URL: str = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/fsh-status-callback")

    # Approval
    APPROVAL_TOKEN_EXPIRY_HOURS: int = int(os.getenv("APPROVAL_TOKEN_EXPIRY_HOURS", "24"))
    APPROVAL_BASE_URL: str = os.getenv("APPROVAL_BASE_URL", "http://localhost:8000")

    # Scoring
    EQUITY_THRESHOLD: float = float(os.getenv("EQUITY_THRESHOLD", "40"))
    MOTIVATION_THRESHOLD: int = int(os.getenv("MOTIVATION_THRESHOLD", "3"))
    MAO_DEFAULT_MULTIPLIER: float = float(os.getenv("MAO_DEFAULT_MULTIPLIER", "0.70"))

    @property
    def db_connection_string(self) -> str:
        """PostgreSQL connection string (without password in URL for safety)."""
        return f"host={self.POSTGRES_HOST} port={self.POSTGRES_PORT} dbname={self.POSTGRES_DB} user={self.POSTGRES_USER}"

    @property
    def has_api_key(self) -> bool:
        """Check if at least one LLM API key is configured."""
        return bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))


# Singleton config instance
config = AetherisConfig()
