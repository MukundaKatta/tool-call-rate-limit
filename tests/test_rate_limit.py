"""Tests for tool-call-rate-limit."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

import pytest
from tool_call_rate_limit import (
    RateLimit,
    RateLimitExceeded,
    RateLimiter,
    make_rate_limiter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeClock:
    """Controllable monotonic clock for tests."""
    def __init__(self, t: float = 0.0):
        self.t = t
    def __call__(self) -> float:
        return self.t
    def advance(self, seconds: float) -> None:
        self.t += seconds


# ---------------------------------------------------------------------------
# RateLimit dataclass
# ---------------------------------------------------------------------------

def test_rate_limit_ok():
    r = RateLimit(calls=5, per_seconds=10.0)
    assert r.calls == 5
    assert r.per_seconds == 10.0

def test_rate_limit_invalid_calls():
    with pytest.raises(ValueError):
        RateLimit(calls=0, per_seconds=10.0)

def test_rate_limit_invalid_per_seconds():
    with pytest.raises(ValueError):
        RateLimit(calls=5, per_seconds=0.0)


# ---------------------------------------------------------------------------
# RateLimiter construction
# ---------------------------------------------------------------------------

def test_no_default_unrestricted():
    limiter = RateLimiter()
    clock = FakeClock()
    limiter._clock = clock
    # No rule — should never fail
    for _ in range(100):
        assert limiter.check("any_tool") is True

def test_default_limit():
    clock = FakeClock()
    limiter = RateLimiter(calls=3, per_seconds=60, clock=clock)
    limiter.check("tool")
    limiter.check("tool")
    limiter.check("tool")
    with pytest.raises(RateLimitExceeded):
        limiter.check("tool")

def test_calls_without_per_seconds_raises():
    with pytest.raises(ValueError):
        RateLimiter(calls=5)

def test_per_seconds_without_calls_raises():
    with pytest.raises(ValueError):
        RateLimiter(per_seconds=10.0)


# ---------------------------------------------------------------------------
# check() — basic
# ---------------------------------------------------------------------------

def test_check_allows_under_limit():
    clock = FakeClock()
    limiter = RateLimiter(calls=5, per_seconds=10, clock=clock)
    for _ in range(5):
        assert limiter.check("search") is True

def test_check_blocks_at_limit():
    clock = FakeClock()
    limiter = RateLimiter(calls=3, per_seconds=10, clock=clock)
    limiter.check("search")
    limiter.check("search")
    limiter.check("search")
    with pytest.raises(RateLimitExceeded):
        limiter.check("search")

def test_check_raises_correct_info():
    clock = FakeClock()
    limiter = RateLimiter(calls=2, per_seconds=10, clock=clock)
    limiter.check("search")
    limiter.check("search")
    try:
        limiter.check("search")
    except RateLimitExceeded as e:
        assert e.tool == "search"
        assert e.calls == 2
        assert e.per_seconds == 10
        assert e.current_count == 2

def test_check_returns_false_no_raise():
    clock = FakeClock()
    limiter = RateLimiter(calls=1, per_seconds=10, clock=clock, raise_on_limit=False)
    limiter.check("tool")
    result = limiter.check("tool")
    assert result is False

def test_window_slides():
    clock = FakeClock(t=0.0)
    limiter = RateLimiter(calls=2, per_seconds=10, clock=clock)
    limiter.check("tool")  # t=0
    limiter.check("tool")  # t=0 — at limit
    clock.advance(11)       # t=11, window has cleared
    assert limiter.check("tool") is True  # allowed again

def test_window_slides_partial():
    clock = FakeClock(t=0.0)
    limiter = RateLimiter(calls=2, per_seconds=10, clock=clock)
    limiter.check("tool")  # t=0
    clock.advance(5)
    limiter.check("tool")  # t=5 — at limit
    clock.advance(6)        # t=11 — first call at t=0 expired, t=5 call still in window
    assert limiter.check("tool") is True   # one slot free
    with pytest.raises(RateLimitExceeded):
        limiter.check("tool")  # now at limit again


# ---------------------------------------------------------------------------
# Per-tool limits
# ---------------------------------------------------------------------------

def test_per_tool_overrides_default():
    clock = FakeClock()
    limiter = RateLimiter(calls=10, per_seconds=60, clock=clock)
    limiter.set_limit("web_search", calls=2, per_seconds=10)
    limiter.check("web_search")
    limiter.check("web_search")
    with pytest.raises(RateLimitExceeded):
        limiter.check("web_search")
    # Other tools still get default limit
    for _ in range(5):
        assert limiter.check("other_tool") is True

def test_remove_limit_falls_back_to_default():
    clock = FakeClock()
    limiter = RateLimiter(calls=10, per_seconds=60, clock=clock)
    limiter.set_limit("tool", calls=1, per_seconds=10)
    limiter.check("tool")
    with pytest.raises(RateLimitExceeded):
        limiter.check("tool")
    limiter.remove_limit("tool")
    limiter.reset("tool")
    # Now limited by default (10/60)
    for _ in range(9):
        assert limiter.check("tool") is True

def test_set_limit_returns_self():
    limiter = RateLimiter()
    result = limiter.set_limit("tool", calls=5, per_seconds=10)
    assert result is limiter

def test_set_limit_chaining():
    limiter = (
        RateLimiter()
        .set_limit("search", calls=3, per_seconds=10)
        .set_limit("file", calls=5, per_seconds=60)
    )
    assert limiter.is_limited("search")
    assert limiter.is_limited("file")


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

def test_reset_specific_tool():
    clock = FakeClock()
    limiter = RateLimiter(calls=2, per_seconds=10, clock=clock)
    limiter.check("search")
    limiter.check("search")
    limiter.reset("search")
    assert limiter.check("search") is True

def test_reset_all():
    clock = FakeClock()
    limiter = RateLimiter(calls=1, per_seconds=10, clock=clock)
    limiter.check("a")
    limiter.check("b")
    limiter.reset()
    assert limiter.check("a") is True
    assert limiter.check("b") is True

def test_reset_unknown_tool_ok():
    limiter = RateLimiter(calls=5, per_seconds=10)
    limiter.reset("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# call_count / remaining
# ---------------------------------------------------------------------------

def test_call_count_zero():
    clock = FakeClock()
    limiter = RateLimiter(calls=5, per_seconds=10, clock=clock)
    assert limiter.call_count("tool") == 0

def test_call_count_increments():
    clock = FakeClock()
    limiter = RateLimiter(calls=5, per_seconds=10, clock=clock)
    limiter.check("tool")
    limiter.check("tool")
    assert limiter.call_count("tool") == 2

def test_call_count_after_window_slides():
    clock = FakeClock(t=0.0)
    limiter = RateLimiter(calls=5, per_seconds=10, clock=clock)
    limiter.check("tool")  # t=0
    clock.advance(11)
    assert limiter.call_count("tool") == 0

def test_remaining_full():
    clock = FakeClock()
    limiter = RateLimiter(calls=5, per_seconds=10, clock=clock)
    assert limiter.remaining("tool") == 5

def test_remaining_decrements():
    clock = FakeClock()
    limiter = RateLimiter(calls=5, per_seconds=10, clock=clock)
    limiter.check("tool")
    limiter.check("tool")
    assert limiter.remaining("tool") == 3

def test_remaining_no_limit_returns_none():
    limiter = RateLimiter()
    assert limiter.remaining("tool") is None


# ---------------------------------------------------------------------------
# is_limited
# ---------------------------------------------------------------------------

def test_is_limited_with_default():
    limiter = RateLimiter(calls=5, per_seconds=10)
    assert limiter.is_limited("anything") is True

def test_is_limited_per_tool():
    limiter = RateLimiter()
    limiter.set_limit("search", calls=2, per_seconds=5)
    assert limiter.is_limited("search") is True

def test_not_limited_no_rule():
    limiter = RateLimiter()
    assert limiter.is_limited("unconfigured") is False


# ---------------------------------------------------------------------------
# retry_after
# ---------------------------------------------------------------------------

def test_retry_after_nonzero():
    clock = FakeClock(t=0.0)
    limiter = RateLimiter(calls=1, per_seconds=10, clock=clock)
    limiter.check("tool")  # t=0
    try:
        limiter.check("tool")
    except RateLimitExceeded as e:
        assert e.retry_after > 0


# ---------------------------------------------------------------------------
# make_rate_limiter factory
# ---------------------------------------------------------------------------

def test_make_rate_limiter():
    limiter = make_rate_limiter(calls=5, per_seconds=60)
    assert limiter.is_limited("any") is True

def test_make_rate_limiter_tool_overrides():
    clock = FakeClock()
    limiter = make_rate_limiter(
        calls=10,
        per_seconds=60,
        tool_limits={"web_search": (2, 10)},
    )
    limiter._clock = clock
    limiter.check("web_search")
    limiter.check("web_search")
    with pytest.raises(RateLimitExceeded):
        limiter.check("web_search")

def test_make_rate_limiter_raise_false():
    clock = FakeClock()
    limiter = make_rate_limiter(calls=1, per_seconds=10, raise_on_limit=False)
    limiter._clock = clock
    limiter.check("tool")
    assert limiter.check("tool") is False


# ---------------------------------------------------------------------------
# repr
# ---------------------------------------------------------------------------

def test_repr_default():
    limiter = RateLimiter(calls=5, per_seconds=10)
    r = repr(limiter)
    assert "RateLimiter" in r

def test_repr_no_default():
    limiter = RateLimiter()
    r = repr(limiter)
    assert "no-default" in r
