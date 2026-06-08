"""Round-robin key selection with per-key cooldown.

Deliberately dependency-free (no Pipecat / OpenAI imports) so it unit-tests on
its own. Used by RotatingGroqLLMService to spread requests across several API
keys and to park a key that hits a rate limit (HTTP 429) on a short cooldown so
the remaining keys keep serving.
"""

from __future__ import annotations

import time
from typing import Callable


class KeyRotator:
    """Hands out key indices round-robin, skipping any currently cooling down.

    `pick()` advances the pointer every call (continuous round-robin), so load
    is spread across keys BEFORE any of them hits a limit — not only after.
    """

    def __init__(
        self,
        n: int,
        *,
        default_cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if n < 1:
            raise ValueError("KeyRotator needs at least one key.")
        self._n = n
        self._default_cooldown = default_cooldown
        self._clock = clock
        self._idx = 0
        self._cool_until = [0.0] * n

    @property
    def size(self) -> int:
        return self._n

    def pick(self) -> int:
        """Return the next usable key index (round-robin), skipping cooled-down
        keys. If every key is cooling down, return the one that frees up soonest."""
        now = self._clock()
        for offset in range(self._n):
            i = (self._idx + offset) % self._n
            if self._cool_until[i] <= now:
                self._idx = (i + 1) % self._n
                return i
        # All keys are cooling down — use the one whose cooldown expires soonest.
        i = min(range(self._n), key=lambda j: self._cool_until[j])
        self._idx = (i + 1) % self._n
        return i

    def cooldown(self, i: int, secs: float | None = None) -> None:
        """Park key `i` for `secs` (or the default) — it'll be skipped until then."""
        duration = self._default_cooldown if secs is None else secs
        self._cool_until[i] = self._clock() + duration
