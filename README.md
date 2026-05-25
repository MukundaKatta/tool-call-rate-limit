# tool-call-rate-limit

Sliding-window rate limiter for agent tool calls — per-tool limits, no dependencies.

Zero dependencies. Python 3.10+. MIT.

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

## License

MIT
