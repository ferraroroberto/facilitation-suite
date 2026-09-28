"""Per-IP rate limits for the public player API (#52): small, in-memory token buckets.

The client address is the **real** phone's: Tailscale Funnel forwards from
loopback with ``X-Forwarded-For`` set to the public client IP (it replaces
any value the client sent — probed through the relay, 2026-09-28) and
uvicorn's ``proxy_headers`` trusts it from ``127.0.0.1`` only.

The limits are generous on purpose: a whole room can sit behind one
corporate NAT, so 60 players share one address — they must all be able to
join within a minute and answer (with retries) within seconds.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

MAX_KEYS = 10_000  # distinct (bucket, ip) pairs kept; full buckets are dropped first


@dataclass(frozen=True)
class Limit:
    burst: float  # tokens a fresh address starts with
    per_s: float  # tokens back per second


# join + resume: 60 phones joining and reloading at once from one NAT, then 5 a second.
JOIN = Limit(burst=200, per_s=5)
# wrong PINs: a typo is fine, a sweep of the 900 000 PINs is not.
WRONG_PIN = Limit(burst=30, per_s=0.5)
# answers: 60 players, each retrying a few times, every question.
ANSWER = Limit(burst=600, per_s=20)


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}  # (name, ip) → (tokens, at)

    def _level(self, name: str, ip: str, limit: Limit, now: float) -> float:
        tokens, at = self._buckets.get((name, ip), (limit.burst, now))
        return min(limit.burst, tokens + (now - at) * limit.per_s)

    def allow(self, name: str, ip: str, limit: Limit) -> float:
        """Take one token: ``0`` when allowed, else the seconds until one is back."""
        now = self.clock()
        tokens = self._level(name, ip, limit, now)
        if tokens < 1:
            self._buckets[(name, ip)] = (tokens, now)
            return (1 - tokens) / limit.per_s
        self._buckets[(name, ip)] = (tokens - 1, now)
        if len(self._buckets) > MAX_KEYS:
            self._prune(now)
        return 0.0

    def peek(self, name: str, ip: str, limit: Limit) -> float:
        """Like ``allow`` without taking a token."""
        tokens = self._level(name, ip, limit, self.clock())
        return 0.0 if tokens >= 1 else (1 - tokens) / limit.per_s

    def _prune(self, now: float) -> None:
        """Drop the entries idle longest (they are full again, or nearly)."""
        for key, _ in sorted(self._buckets.items(), key=lambda kv: kv[1][1])[: len(self._buckets) - MAX_KEYS // 2]:
            del self._buckets[key]
