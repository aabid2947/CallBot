"""Core configuration.

Loaded from environment variables (optionally via a local `.env` file).
This module only knows about CORE concerns. It must never contain web,
HTTP, or transport settings — those belong to the `server/` layer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    # Optional convenience for local dev. Production can rely on real env vars.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a dev convenience only
    pass


@dataclass(frozen=True)
class CoreSettings:
    """Immutable core settings.

    Attributes:
        groq_api_key: API key for the Groq LLM (the agent brain).
        deepgram_api_key: API key for Deepgram STT + Aura TTS.
        database_url: SQLAlchemy URL for the booking DB.
            Defaults to a local SQLite file.
    """

    groq_api_key: str
    deepgram_api_key: str
    database_url: str

    @staticmethod
    def from_env() -> "CoreSettings":
        return CoreSettings(
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY", ""),
            database_url=os.getenv("DATABASE_URL", "sqlite:///voicestream.db"),
        )


def get_settings() -> CoreSettings:
    """Return core settings loaded from the environment."""
    return CoreSettings.from_env()
