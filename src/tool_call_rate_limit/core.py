"""Sliding-window rate limiter for agent tool calls.

Each tool name can have an independent limit: N calls per T seconds.
A global (default) limit applies to tools without a specific rule.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


class RateLimitExceeded(Exception):
    """Raised when a tool call exceeds its rate limit."""

    def __init__(
        self,
        tool: str,
        calls: int,
        per_seconds: float,
        current_count: int,
        retry_after: float,
    ) -> None:
        self.tool = tool
        self.calls = calls
        self.per_seconds = per_seconds
        self.current_count = current_count
        self.retry_after = retry_after
        super().__init__(
            f"rate limit exceeded for {tool!r}: "
            f"{current_count} calls in {per_seconds}s "
            f"(limit {calls}); retry after {retry_after:.2f}s"
        )


@dataclass
class RateLimit:
    """A rate limit rule: at most ``calls`` per ``per_seconds`` window.

    Attributes:
        calls: maximum number of calls allowed in the window.
        per_seconds: duration of the sliding window in seconds.
    """

    calls: int
    per_seconds: float

    def __post_init__(self) -> None:
        if self.calls <= 0:
            raise ValueError(f"calls must be > 0, got {self.calls}")
        if self.per_seconds <= 0:
            raise ValueError(f"per_seconds must be > 0, got {self.per_seconds}")


class RateLimiter:
    """Sliding-window rate limiter for named agent tools.

    A default limit applies to all tools unless overridden by a per-tool rule.
    If no default is set, tools without a specific rule are unrestricted.

    Args:
        calls: default max calls per window (required if per_seconds given).
        per_seconds: default window size in seconds (required if calls given).
        raise_on_limit: if True (default), raise RateLimitExceeded when the
            limit is hit. If False, ``check()`` returns False instead.
        clock: callable returning current time (default: time.monotonic).

    Example::

        limiter = RateLimiter(calls=10, per_seconds=60)
        limiter.check("web_search")  # ok or raises RateLimitExceeded

        limiter.set_limit("web_search", calls=2, per_seconds=10)
    """

    def __init__(
        self,
        *,
        calls: int | None = None,
        per_seconds: float | None = None,
        raise_on_limit: bool = True,
        clock: Any = None,
    ) -> None:
        self._clock = clock if clock is not None else time.monotonic
        self.raise_on_limit = raise_on_limit
        self._default: RateLimit | None = None
        if calls is not None or per_seconds is not None:
            if calls is None or per_seconds is None:
                raise ValueError("calls and per_seconds must both be provided")
            self._default = RateLimit(calls=calls, per_seconds=per_seconds)

        # Per-tool rules
        self._limits: dict[str, RateLimit] = {}
        # Sliding window: tool → deque of timestamps
        self._windows: dict[str, deque[float]] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_limit(self, tool: str, *, calls: int, per_seconds: float) -> "RateLimiter":
        """Set a per-tool rate limit.

        Args:
            tool: tool name.
            calls: max calls allowed in the window.
            per_seconds: window duration in seconds.

        Returns:
            self, for chaining.
        """
        self._limits[tool] = RateLimit(calls=calls, per_seconds=per_seconds)
        return self

    def remove_limit(self, tool: str) -> None:
        """Remove a per-tool rule (falls back to default)."""
        self._limits.pop(tool, None)

    def reset(self, tool: str | None = None) -> None:
        """Reset call history for a specific tool or all tools."""
        if tool is None:
            self._windows.clear()
        else:
            self._windows.pop(tool, None)

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(self, tool: str) -> bool:
        """Check and record a call attempt for *tool*.

        Args:
            tool: name of the tool being called.

        Returns:
            True if the call is allowed.

        Raises:
            RateLimitExceeded: if raise_on_limit is True and the limit is hit.
        """
        limit = self._limits.get(tool) or self._default
        now = self._clock()

        if limit is None:
            # No rule for this tool — unrestricted.
            return True

        window = self._windows.setdefault(tool, deque())

        # Evict timestamps older than the window.
        cutoff = now - limit.per_seconds
        while window and window[0] <= cutoff:
            window.popleft()

        current = len(window)

        if current >= limit.calls:
            # Compute how long until the oldest call expires.
            retry_after = (window[0] + limit.per_seconds) - now if window else 0.0
            if self.raise_on_limit:
                raise RateLimitExceeded(
                    tool=tool,
                    calls=limit.calls,
                    per_seconds=limit.per_seconds,
                    current_count=current,
                    retry_after=max(0.0, retry_after),
                )
            return False

        window.append(now)
        return True

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def call_count(self, tool: str) -> int:
        """Return the number of calls recorded in the current window for *tool*."""
        limit = self._limits.get(tool) or self._default
        if limit is None:
            return len(self._windows.get(tool, deque()))
        window = self._windows.get(tool, deque())
        now = self._clock()
        cutoff = now - limit.per_seconds
        return sum(1 for t in window if t > cutoff)

    def remaining(self, tool: str) -> int | None:
        """Return remaining calls in the current window, or None if no limit."""
        limit = self._limits.get(tool) or self._default
        if limit is None:
            return None
        return max(0, limit.calls - self.call_count(tool))

    def is_limited(self, tool: str) -> bool:
        """Return True if *tool* has a rate limit rule configured."""
        return tool in self._limits or self._default is not None

    def __repr__(self) -> str:
        default = f"default={self._default}" if self._default else "no-default"
        return f"RateLimiter({default}, tools={list(self._limits)})"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_rate_limiter(
    calls: int,
    per_seconds: float,
    *,
    raise_on_limit: bool = True,
    tool_limits: dict[str, tuple[int, float]] | None = None,
) -> RateLimiter:
    """Create a RateLimiter with a default limit and optional per-tool overrides.

    Args:
        calls: default max calls per window.
        per_seconds: default window in seconds.
        raise_on_limit: raise on limit exceeded (default True).
        tool_limits: optional dict of {tool: (calls, per_seconds)} overrides.

    Example::

        limiter = make_rate_limiter(
            calls=10, per_seconds=60,
            tool_limits={"web_search": (2, 10)},
        )
    """
    limiter = RateLimiter(
        calls=calls,
        per_seconds=per_seconds,
        raise_on_limit=raise_on_limit,
    )
    if tool_limits:
        for tool, (c, s) in tool_limits.items():
            limiter.set_limit(tool, calls=c, per_seconds=s)
    return limiter
