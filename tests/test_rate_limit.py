"""Tests for tool-call-rate-limit.

These tests use only the standard-library :mod:`unittest` framework so they
run with no third-party dependencies. Run them with::

    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

from tool_call_rate_limit import (  # noqa: E402
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

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ---------------------------------------------------------------------------
# RateLimit dataclass
# ---------------------------------------------------------------------------

class TestRateLimit(unittest.TestCase):
    def test_rate_limit_ok(self):
        r = RateLimit(calls=5, per_seconds=10.0)
        self.assertEqual(r.calls, 5)
        self.assertEqual(r.per_seconds, 10.0)

    def test_rate_limit_invalid_calls(self):
        with self.assertRaises(ValueError):
            RateLimit(calls=0, per_seconds=10.0)

    def test_rate_limit_invalid_negative_calls(self):
        with self.assertRaises(ValueError):
            RateLimit(calls=-1, per_seconds=10.0)

    def test_rate_limit_invalid_per_seconds(self):
        with self.assertRaises(ValueError):
            RateLimit(calls=5, per_seconds=0.0)


# ---------------------------------------------------------------------------
# RateLimiter construction
# ---------------------------------------------------------------------------

class TestConstruction(unittest.TestCase):
    def test_no_default_unrestricted(self):
        limiter = RateLimiter(clock=FakeClock())
        # No rule — should never fail
        for _ in range(100):
            self.assertTrue(limiter.check("any_tool"))

    def test_default_limit(self):
        limiter = RateLimiter(calls=3, per_seconds=60, clock=FakeClock())
        limiter.check("tool")
        limiter.check("tool")
        limiter.check("tool")
        with self.assertRaises(RateLimitExceeded):
            limiter.check("tool")

    def test_calls_without_per_seconds_raises(self):
        with self.assertRaises(ValueError):
            RateLimiter(calls=5)

    def test_per_seconds_without_calls_raises(self):
        with self.assertRaises(ValueError):
            RateLimiter(per_seconds=10.0)


# ---------------------------------------------------------------------------
# check() — basic
# ---------------------------------------------------------------------------

class TestCheck(unittest.TestCase):
    def test_check_allows_under_limit(self):
        limiter = RateLimiter(calls=5, per_seconds=10, clock=FakeClock())
        for _ in range(5):
            self.assertTrue(limiter.check("search"))

    def test_check_blocks_at_limit(self):
        limiter = RateLimiter(calls=3, per_seconds=10, clock=FakeClock())
        limiter.check("search")
        limiter.check("search")
        limiter.check("search")
        with self.assertRaises(RateLimitExceeded):
            limiter.check("search")

    def test_check_raises_correct_info(self):
        limiter = RateLimiter(calls=2, per_seconds=10, clock=FakeClock())
        limiter.check("search")
        limiter.check("search")
        with self.assertRaises(RateLimitExceeded) as ctx:
            limiter.check("search")
        e = ctx.exception
        self.assertEqual(e.tool, "search")
        self.assertEqual(e.calls, 2)
        self.assertEqual(e.per_seconds, 10)
        self.assertEqual(e.current_count, 2)

    def test_check_exception_message(self):
        limiter = RateLimiter(calls=1, per_seconds=10, clock=FakeClock())
        limiter.check("search")
        with self.assertRaises(RateLimitExceeded) as ctx:
            limiter.check("search")
        msg = str(ctx.exception)
        self.assertIn("search", msg)
        self.assertIn("rate limit exceeded", msg)

    def test_check_returns_false_no_raise(self):
        limiter = RateLimiter(
            calls=1, per_seconds=10, clock=FakeClock(), raise_on_limit=False
        )
        limiter.check("tool")
        self.assertFalse(limiter.check("tool"))

    def test_blocked_call_is_not_recorded(self):
        # A rejected call (raise_on_limit=False) must not consume a slot once
        # the window slides, otherwise the limiter would be permanently stuck.
        clock = FakeClock(t=0.0)
        limiter = RateLimiter(
            calls=1, per_seconds=10, clock=clock, raise_on_limit=False
        )
        self.assertTrue(limiter.check("tool"))   # t=0, recorded
        self.assertFalse(limiter.check("tool"))  # t=0, rejected, not recorded
        self.assertFalse(limiter.check("tool"))  # still rejected
        clock.advance(11)                         # original call expires
        self.assertTrue(limiter.check("tool"))   # slot free again

    def test_window_slides(self):
        clock = FakeClock(t=0.0)
        limiter = RateLimiter(calls=2, per_seconds=10, clock=clock)
        limiter.check("tool")  # t=0
        limiter.check("tool")  # t=0 — at limit
        clock.advance(11)      # t=11, window has cleared
        self.assertTrue(limiter.check("tool"))  # allowed again

    def test_window_slides_partial(self):
        clock = FakeClock(t=0.0)
        limiter = RateLimiter(calls=2, per_seconds=10, clock=clock)
        limiter.check("tool")  # t=0
        clock.advance(5)
        limiter.check("tool")  # t=5 — at limit
        clock.advance(6)       # t=11 — t=0 call expired, t=5 still in window
        self.assertTrue(limiter.check("tool"))  # one slot free
        with self.assertRaises(RateLimitExceeded):
            limiter.check("tool")  # now at limit again


# ---------------------------------------------------------------------------
# Per-tool limits
# ---------------------------------------------------------------------------

class TestPerToolLimits(unittest.TestCase):
    def test_per_tool_overrides_default(self):
        limiter = RateLimiter(calls=10, per_seconds=60, clock=FakeClock())
        limiter.set_limit("web_search", calls=2, per_seconds=10)
        limiter.check("web_search")
        limiter.check("web_search")
        with self.assertRaises(RateLimitExceeded):
            limiter.check("web_search")
        # Other tools still get default limit
        for _ in range(5):
            self.assertTrue(limiter.check("other_tool"))

    def test_remove_limit_falls_back_to_default(self):
        limiter = RateLimiter(calls=10, per_seconds=60, clock=FakeClock())
        limiter.set_limit("tool", calls=1, per_seconds=10)
        limiter.check("tool")
        with self.assertRaises(RateLimitExceeded):
            limiter.check("tool")
        limiter.remove_limit("tool")
        limiter.reset("tool")
        # Now limited by default (10/60)
        for _ in range(9):
            self.assertTrue(limiter.check("tool"))

    def test_remove_unknown_limit_ok(self):
        limiter = RateLimiter(calls=5, per_seconds=10)
        limiter.remove_limit("never_set")  # should not raise

    def test_set_limit_returns_self(self):
        limiter = RateLimiter()
        self.assertIs(limiter.set_limit("tool", calls=5, per_seconds=10), limiter)

    def test_set_limit_chaining(self):
        limiter = (
            RateLimiter()
            .set_limit("search", calls=3, per_seconds=10)
            .set_limit("file", calls=5, per_seconds=60)
        )
        self.assertTrue(limiter.is_limited("search"))
        self.assertTrue(limiter.is_limited("file"))

    def test_set_limit_validates(self):
        limiter = RateLimiter()
        with self.assertRaises(ValueError):
            limiter.set_limit("tool", calls=0, per_seconds=10)


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

class TestReset(unittest.TestCase):
    def test_reset_specific_tool(self):
        limiter = RateLimiter(calls=2, per_seconds=10, clock=FakeClock())
        limiter.check("search")
        limiter.check("search")
        limiter.reset("search")
        self.assertTrue(limiter.check("search"))

    def test_reset_all(self):
        limiter = RateLimiter(calls=1, per_seconds=10, clock=FakeClock())
        limiter.check("a")
        limiter.check("b")
        limiter.reset()
        self.assertTrue(limiter.check("a"))
        self.assertTrue(limiter.check("b"))

    def test_reset_unknown_tool_ok(self):
        limiter = RateLimiter(calls=5, per_seconds=10)
        limiter.reset("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# call_count / remaining
# ---------------------------------------------------------------------------

class TestInspection(unittest.TestCase):
    def test_call_count_zero(self):
        limiter = RateLimiter(calls=5, per_seconds=10, clock=FakeClock())
        self.assertEqual(limiter.call_count("tool"), 0)

    def test_call_count_increments(self):
        limiter = RateLimiter(calls=5, per_seconds=10, clock=FakeClock())
        limiter.check("tool")
        limiter.check("tool")
        self.assertEqual(limiter.call_count("tool"), 2)

    def test_call_count_after_window_slides(self):
        clock = FakeClock(t=0.0)
        limiter = RateLimiter(calls=5, per_seconds=10, clock=clock)
        limiter.check("tool")  # t=0
        clock.advance(11)
        self.assertEqual(limiter.call_count("tool"), 0)

    def test_call_count_no_limit(self):
        # With no rule, call_count still reflects raw recorded history (none,
        # because unrestricted tools are never recorded).
        limiter = RateLimiter(clock=FakeClock())
        limiter.check("tool")
        self.assertEqual(limiter.call_count("tool"), 0)

    def test_remaining_full(self):
        limiter = RateLimiter(calls=5, per_seconds=10, clock=FakeClock())
        self.assertEqual(limiter.remaining("tool"), 5)

    def test_remaining_decrements(self):
        limiter = RateLimiter(calls=5, per_seconds=10, clock=FakeClock())
        limiter.check("tool")
        limiter.check("tool")
        self.assertEqual(limiter.remaining("tool"), 3)

    def test_remaining_clamps_at_zero(self):
        limiter = RateLimiter(
            calls=2, per_seconds=10, clock=FakeClock(), raise_on_limit=False
        )
        limiter.check("tool")
        limiter.check("tool")
        self.assertEqual(limiter.remaining("tool"), 0)

    def test_remaining_no_limit_returns_none(self):
        limiter = RateLimiter()
        self.assertIsNone(limiter.remaining("tool"))


# ---------------------------------------------------------------------------
# is_limited
# ---------------------------------------------------------------------------

class TestIsLimited(unittest.TestCase):
    def test_is_limited_with_default(self):
        limiter = RateLimiter(calls=5, per_seconds=10)
        self.assertTrue(limiter.is_limited("anything"))

    def test_is_limited_per_tool(self):
        limiter = RateLimiter()
        limiter.set_limit("search", calls=2, per_seconds=5)
        self.assertTrue(limiter.is_limited("search"))

    def test_not_limited_no_rule(self):
        limiter = RateLimiter()
        self.assertFalse(limiter.is_limited("unconfigured"))


# ---------------------------------------------------------------------------
# retry_after
# ---------------------------------------------------------------------------

class TestRetryAfter(unittest.TestCase):
    def test_retry_after_nonzero(self):
        clock = FakeClock(t=0.0)
        limiter = RateLimiter(calls=1, per_seconds=10, clock=clock)
        limiter.check("tool")  # t=0
        with self.assertRaises(RateLimitExceeded) as ctx:
            limiter.check("tool")
        self.assertGreater(ctx.exception.retry_after, 0)

    def test_retry_after_shrinks_over_time(self):
        clock = FakeClock(t=0.0)
        limiter = RateLimiter(calls=1, per_seconds=10, clock=clock)
        limiter.check("tool")  # t=0
        with self.assertRaises(RateLimitExceeded) as first:
            limiter.check("tool")
        clock.advance(4)
        with self.assertRaises(RateLimitExceeded) as second:
            limiter.check("tool")
        self.assertLess(second.exception.retry_after, first.exception.retry_after)


# ---------------------------------------------------------------------------
# make_rate_limiter factory
# ---------------------------------------------------------------------------

class TestFactory(unittest.TestCase):
    def test_make_rate_limiter(self):
        limiter = make_rate_limiter(calls=5, per_seconds=60)
        self.assertTrue(limiter.is_limited("any"))

    def test_make_rate_limiter_no_tool_limits(self):
        # Default applies; no per-tool rules are registered.
        limiter = make_rate_limiter(calls=5, per_seconds=60)
        limiter._clock = FakeClock()
        self.assertTrue(limiter.is_limited("foo"))
        for _ in range(5):
            self.assertTrue(limiter.check("foo"))
        with self.assertRaises(RateLimitExceeded):
            limiter.check("foo")

    def test_make_rate_limiter_tool_overrides(self):
        limiter = make_rate_limiter(
            calls=10,
            per_seconds=60,
            tool_limits={"web_search": (2, 10)},
        )
        limiter._clock = FakeClock()
        limiter.check("web_search")
        limiter.check("web_search")
        with self.assertRaises(RateLimitExceeded):
            limiter.check("web_search")

    def test_make_rate_limiter_raise_false(self):
        limiter = make_rate_limiter(calls=1, per_seconds=10, raise_on_limit=False)
        limiter._clock = FakeClock()
        limiter.check("tool")
        self.assertFalse(limiter.check("tool"))


# ---------------------------------------------------------------------------
# repr
# ---------------------------------------------------------------------------

class TestRepr(unittest.TestCase):
    def test_repr_default(self):
        limiter = RateLimiter(calls=5, per_seconds=10)
        self.assertIn("RateLimiter", repr(limiter))

    def test_repr_no_default(self):
        limiter = RateLimiter()
        self.assertIn("no-default", repr(limiter))


if __name__ == "__main__":
    unittest.main()
