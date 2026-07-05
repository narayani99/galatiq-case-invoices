"""
Environment-based configuration for the Galatiq Invoice Processing System.

Loads settings from .env file and provides:
- Settings singleton with all env vars
- get_llm_client() for OpenAI-compatible xAI Grok access
- get_logger() for JSON structured logging
"""

from __future__ import annotations

import json
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env file from project root (parent of 'src/')
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


# ---------------------------------------------------------------------------
# Settings (singleton via lru_cache)
# ---------------------------------------------------------------------------
class Settings:
    """Centralised application settings populated from environment variables.

    All values fall back to sensible defaults so the app can start in
    development without a .env file (with appropriate warnings).
    """

    def __init__(self) -> None:
        # -- LLM --
        self.GROK_API_KEY: str = self._load_api_key()
        self.GROK_MODEL: str = os.getenv("GROK_MODEL", "grok-3-mini")
        self.GROK_API_BASE: str = os.getenv(
            "GROK_API_BASE", "https://api.x.ai/v1"
        )

        # -- Fallback LLM --
        self.FALLBACK_LLM: str = os.getenv("FALLBACK_LLM", "openai")
        self.OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

        # -- Database --
        self.DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./inventory.db")
        self.DATABASE_TIMEOUT: int = int(os.getenv("DATABASE_TIMEOUT", "30"))

        # -- Logging --
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
        self.LOG_FORMAT: str = os.getenv("LOG_FORMAT", "text").lower()

        # -- API --
        self.API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
        self.API_PORT: int = int(os.getenv("API_PORT", "8000"))
        self.API_WORKERS: int = int(os.getenv("API_WORKERS", "4"))

        # -- Frontend --
        self.REACT_APP_API_URL: str = os.getenv(
            "REACT_APP_API_URL", "http://localhost:8000"
        )

        # -- Feature flags --
        self.ENABLE_RESILIENCE_TESTS: bool = (
            os.getenv("ENABLE_RESILIENCE_TESTS", "false").lower() == "true"
        )
        self.ENABLE_MOCK_PAYMENT_FAILURES: bool = (
            os.getenv("ENABLE_MOCK_PAYMENT_FAILURES", "true").lower() == "true"
        )
        self.MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "2"))
        self.RETRY_BACKOFF_FACTOR: int = int(
            os.getenv("RETRY_BACKOFF_FACTOR", "2")
        )

    # Convenience helpers ────────────────────────────────────────────────

    @staticmethod
    def _load_api_key() -> str:
        """Load Grok API key from env var or .api-key file.

        Priority:
            1. ``GROK_API_KEY`` environment variable
            2. ``.api-key`` file in the project root
        """
        key = os.getenv("GROK_API_KEY", "").strip()
        if key:
            return key

        api_key_file = _PROJECT_ROOT / ".api-key"
        if api_key_file.exists():
            key = api_key_file.read_text(encoding="utf-8").strip()
            if key:
                return key

        return ""

    @property
    def database_path_resolved(self) -> Path:
        """Return an absolute ``Path`` for the database file."""
        p = Path(self.DATABASE_PATH)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        return p

    @property
    def is_api_key_configured(self) -> bool:
        """Return ``True`` if a real Grok API key is present."""
        return bool(
            self.GROK_API_KEY
            and self.GROK_API_KEY not in ("your_api_key_here", "")
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton :class:`Settings` instance."""
    return Settings()


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

def get_llm_client():
    """Return an *OpenAI-compatible* client pointed at the xAI Grok API.

    The client is created even if the API key is a placeholder so that
    downstream code can instantiate objects; a warning is logged instead.

    Returns:
        openai.OpenAI: Configured OpenAI client instance.
    """
    from openai import OpenAI  # deferred to avoid import-time side effects

    settings = get_settings()

    if not settings.is_api_key_configured:
        logger = get_logger("config")
        logger.warning(
            "GROK_API_KEY is not set or is a placeholder. "
            "LLM calls will fail until a valid key is provided."
        )

    client = OpenAI(
        api_key=settings.GROK_API_KEY or "placeholder-key",
        base_url=settings.GROK_API_BASE,
    )
    return client


# ---------------------------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------------------------

class _JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Merge any extra fields attached via the `extra` kwarg
        for key in ("invoice_id", "agent_name", "action", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value
        return json.dumps(log_entry, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured with JSON structured output.

    Args:
        name: Logical name for the logger (e.g. ``"ingestion"``, ``"database"``).

    Returns:
        logging.Logger: Configured logger instance.
    """
    settings = get_settings()
    logger = logging.getLogger(f"galatiq.{name}")

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)

        if settings.LOG_FORMAT == "json":
            handler.setFormatter(_JSONFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
                )
            )

        logger.addHandler(handler)
        logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
        logger.propagate = False

    return logger
