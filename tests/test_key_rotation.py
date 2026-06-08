"""KeyRotator: round-robin selection + per-key cooldown. Pure logic, no network."""

import pytest

from voice.key_rotation import KeyRotator


class _Clock:
    """A controllable monotonic clock for deterministic cooldown tests."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_round_robin_advances_every_pick():
    r = KeyRotator(3, clock=_Clock())
    assert [r.pick() for _ in range(7)] == [0, 1, 2, 0, 1, 2, 0]


def test_cooldown_skips_the_parked_key():
    clk = _Clock()
    r = KeyRotator(3, default_cooldown=30.0, clock=clk)
    assert r.pick() == 0
    r.cooldown(1)  # park key 1 for 30s
    # next picks skip 1 -> 2, then 0, then 2 again (1 still cooling)
    assert r.pick() == 2
    assert r.pick() == 0
    assert r.pick() == 2
    # after the cooldown elapses, key 1 is back in rotation
    clk.t += 31.0
    got = {r.pick() for _ in range(3)}
    assert 1 in got


def test_all_cooled_returns_soonest_to_recover():
    clk = _Clock()
    r = KeyRotator(3, clock=clk)
    r.cooldown(0, 50.0)
    r.cooldown(1, 10.0)  # frees up first
    r.cooldown(2, 40.0)
    assert r.pick() == 1


def test_retry_after_override_is_used():
    clk = _Clock()
    r = KeyRotator(2, default_cooldown=30.0, clock=clk)
    r.cooldown(0, 5.0)  # explicit (e.g. from Retry-After), shorter than default
    clk.t += 6.0
    # key 0 recovered after 5s, so it's available again
    assert 0 in {r.pick() for _ in range(2)}


def test_requires_at_least_one_key():
    with pytest.raises(ValueError):
        KeyRotator(0)
