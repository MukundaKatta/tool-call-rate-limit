# tool-call-rate-limit

[![CI](https://github.com/MukundaKatta/tool-call-rate-limit/actions/workflows/ci.yml/badge.svg)](https://github.com/MukundaKatta/tool-call-rate-limit/actions/workflows/ci.yml)

Sliding-window rate limiter for agent tool calls — per-tool limits, no dependencies.

Zero dependencies. Python 3.10+. MIT.

## Why

When an LLM agent drives a loop of tool calls, a single bad plan can hammer an
expensive or fragile tool (a web search API, a shell command, a paid endpoint)
dozens of times in seconds. `RateLimiter` gives you a tiny, in-process guard
rail: register a default budget plus tight per-tool overrides, then call
`check(tool)` in your dispatch path. It uses a true **sliding window** (it
remembers individual call timestamps rather than resetting on a fixed interval),
so bursts at a window boundary cannot sneak past the limit.

## Install

```bash
pip install tool-call-rate-limit
```

## Usage

```python
from tool_call_rate_limit import RateLimiter

# Global default: 10 calls per 60 seconds
limiter = RateLimiter(calls=10, per_seconds=60)

# In your tool dispatch:
limiter.check("web_search")   # ok or raises RateLimitExceeded
result = do_web_search(query)
```

## Per-tool limits

```python
limiter = RateLimiter(calls=20, per_seconds=60)  # default
limiter.set_limit("web_search", calls=3, per_seconds=10)  # override

limiter.check("web_search")   # tight limit
limiter.check("read_file")    # uses default
```

## Don't raise, return False

```python
limiter = RateLimiter(calls=5, per_seconds=10, raise_on_limit=False)
if not limiter.check("tool"):
    # handle rate limit
    pass
```

## Inspect state

```python
limiter.call_count("web_search")   # calls in current window
limiter.remaining("web_search")    # calls left before limit
limiter.is_limited("web_search")   # True if a rule applies
```

## Reset

```python
limiter.reset("web_search")   # clear history for one tool
limiter.reset()               # clear all
```

## Factory

```python
from tool_call_rate_limit import make_rate_limiter

limiter = make_rate_limiter(
    calls=10, per_seconds=60,
    tool_limits={"web_search": (2, 10)},
)
```

## RateLimitExceeded

```python
try:
    limiter.check("web_search")
except RateLimitExceeded as e:
    print(e.tool)          # "web_search"
    print(e.retry_after)   # seconds until a slot opens
    print(e.current_count) # calls recorded in window
```

## Deterministic time in tests

`RateLimiter` accepts a `clock` callable (defaulting to `time.monotonic`) so you
can drive time forward by hand instead of sleeping in tests:

```python
class FakeClock:
    def __init__(self, t=0.0):
        self.t = t
    def __call__(self):
        return self.t
    def advance(self, seconds):
        self.t += seconds

clock = FakeClock()
limiter = RateLimiter(calls=2, per_seconds=10, clock=clock)
limiter.check("tool")
limiter.check("tool")          # at limit
clock.advance(11)              # window slides
limiter.check("tool")          # allowed again
```

## Development

The package and its test suite have no third-party dependencies. Run the tests
with the standard library only:

```bash
python -m unittest discover -s tests -v
```

CI runs this suite on Python 3.10–3.13 (see `.github/workflows/ci.yml`).

## License

MIT
