"""Voice-layer configuration and fail-fast validation.

Reuses core's env settings for the API keys and adds voice-only knobs
(model/voice choices). Calling `load_voice_settings()` raises a clear,
actionable error if a required key is missing — we fail fast rather than
deep inside Pipecat at call time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core.config import get_settings


class VoiceConfigError(RuntimeError):
    """Raised when required voice configuration is missing/invalid."""


def _groq_api_keys(primary: str) -> tuple[str, ...]:
    """All configured Groq keys, in order: GROQ_API_KEY, then GROQ_API_KEY_2..10.
    De-duped, blanks dropped. Multiple keys enable round-robin rotation (see
    RotatingGroqLLMService) so we don't hammer one key into a rate limit."""
    keys: list[str] = []
    if primary and primary.strip():
        keys.append(primary.strip())
    for n in range(2, 11):
        value = (os.getenv(f"GROQ_API_KEY_{n}") or "").strip()
        if value:
            keys.append(value)
    return tuple(dict.fromkeys(keys))  # preserve order, drop dups


@dataclass(frozen=True)
class VoiceSettings:
    """Everything the pipeline needs, resolved from the environment."""

    groq_api_key: str
    deepgram_api_key: str
    llm_model: str
    stt_model: str
    tts_voice: str
    business_name: str
    # All Groq keys (primary + GROQ_API_KEY_2..N) for round-robin rotation.
    # Defaults empty so existing constructions (tests) keep working.
    groq_api_keys: tuple[str, ...] = ()

    @staticmethod
    def from_env() -> "VoiceSettings":
        core = get_settings()
        return VoiceSettings(
            groq_api_key=core.groq_api_key,
            deepgram_api_key=core.deepgram_api_key,
            # Fast, current defaults; override via .env without code changes.
            llm_model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
            stt_model=os.getenv("STT_MODEL", "nova-3"),
            tts_voice=os.getenv("TTS_VOICE", "aura-2-thalia-en"),
            business_name=os.getenv("BUSINESS_NAME", "our office"),
            groq_api_keys=_groq_api_keys(core.groq_api_key),
        )


def load_voice_settings() -> VoiceSettings:
    """Load and validate voice settings, failing fast with guidance."""
    s = VoiceSettings.from_env()
    missing: list[str] = []
    if not s.groq_api_key:
        missing.append("GROQ_API_KEY (free: https://console.groq.com)")
    if not s.deepgram_api_key:
        missing.append("DEEPGRAM_API_KEY (free: https://console.deepgram.com)")
    if missing:
        raise VoiceConfigError(
            "Missing required voice configuration: "
            + "; ".join(missing)
            + ". Copy .env.example to .env and fill these in."
        )
    return s
