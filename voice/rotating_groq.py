"""Round-robin Groq API-key rotation with rate-limit cooldown.

VoiceStream serves one call at a time and re-sends the full context every turn
(no per-key server state), so requests can be spread across several Groq API
keys round-robin — staying under each key's per-minute limit instead of
hammering one until it 429s. A key that DOES hit a 429 is parked on a short
cooldown and skipped; the remaining keys carry the call. Mirrors AIVA's Gemini
key rotation.

Lives in the voice layer (it imports Pipecat + the OpenAI client), so it never
touches core/.
"""

from __future__ import annotations

from typing import Sequence

from loguru import logger
from openai import AuthenticationError, PermissionDeniedError, RateLimitError
from pipecat.services.groq.llm import GroqLLMService

from .key_rotation import KeyRotator

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Park a key that's outright rejected (bad/expired key, 401/403) for a long time
# so round-robin doesn't keep sending ~1/N of turns to a dead key mid-call.
_AUTH_COOLDOWN = 3600.0


class RotatingGroqLLMService(GroqLLMService):
    """A GroqLLMService that round-robins across multiple API keys per request
    and cools a key down on HTTP 429, transparently retrying on the next key."""

    def __init__(
        self,
        *,
        api_keys: Sequence[str],
        base_url: str = _GROQ_BASE_URL,
        **kwargs,
    ) -> None:
        # De-dupe, drop blanks, preserve order.
        keys = [k.strip() for k in dict.fromkeys(api_keys) if k and k.strip()]
        if not keys:
            raise ValueError("RotatingGroqLLMService requires at least one Groq API key.")
        super().__init__(api_key=keys[0], base_url=base_url, **kwargs)
        self._keys = keys
        # One client per key; clients[0] is the base's own client (key0).
        self._clients = [self._client] + [
            self.create_client(api_key=k, base_url=base_url) for k in keys[1:]
        ]
        self._rotator = KeyRotator(len(keys))
        logger.info(
            "Groq key rotation enabled: {} keys, round-robin with 429 cooldown.", len(keys)
        )

    async def get_chat_completions(self, context):
        """Pick the next healthy key, run the completion, and on a 429 cool that
        key down and retry on the next one. Raises only if EVERY key is limited."""
        last_exc: Exception | None = None
        for _ in range(len(self._keys)):
            i = self._rotator.pick()
            self._client = self._clients[i]  # the base method reads self._client
            try:
                return await super().get_chat_completions(context)
            except RateLimitError as exc:
                self._rotator.cooldown(i, _retry_after(exc))
                last_exc = exc
                logger.warning(
                    "Groq key #{}/{} hit a 429 — cooling it down and rotating to the next.",
                    i + 1,
                    len(self._keys),
                )
            except (AuthenticationError, PermissionDeniedError) as exc:
                self._rotator.cooldown(i, _AUTH_COOLDOWN)
                last_exc = exc
                logger.error(
                    "Groq key #{}/{} was rejected (auth/permission) — parking it and rotating. "
                    "Check that key is valid.",
                    i + 1,
                    len(self._keys),
                )
        logger.error("All {} Groq keys are unavailable (rate-limited or rejected) right now.", len(self._keys))
        assert last_exc is not None  # the loop ran at least once
        raise last_exc


def _retry_after(exc: RateLimitError) -> float | None:
    """Honor Groq's Retry-After header (seconds, capped) when present; else None
    so the rotator uses its default cooldown."""
    try:
        retry_after = exc.response.headers.get("retry-after")  # type: ignore[union-attr]
        if retry_after:
            return min(float(retry_after), 60.0)
    except Exception:  # noqa: BLE001 - header is best-effort
        pass
    return None
