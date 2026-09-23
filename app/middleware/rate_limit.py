"""In-process sliding-window rate limiter.

Good enough for a single-process deployment and for the demo. For multiple
workers or hosts, back this with Redis - the interface below stays the same.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from app.config import settings


class RateLimiter:
    def __init__(self, attempts: int, window_seconds: int, name: str = "default"):
        self.attempts = attempts
        self.window = window_seconds
        self.name = name
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Deque[float]:
        bucket = self._hits[key]
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return bucket

    def check(self, key: str) -> Tuple[bool, int]:
        """Return (allowed, retry_after_seconds) WITHOUT consuming a slot."""
        now = time.time()
        with self._lock:
            bucket = self._prune(key, now)
            if len(bucket) >= self.attempts:
                return False, max(1, int(self.window - (now - bucket[0])))
            return True, 0

    def hit(self, key: str) -> Tuple[bool, int]:
        """Consume a slot. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        with self._lock:
            bucket = self._prune(key, now)
            if len(bucket) >= self.attempts:
                return False, max(1, int(self.window - (now - bucket[0])))
            bucket.append(now)
            return True, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


login_rate_limiter = RateLimiter(
    attempts=settings.login_rate_limit_attempts,
    window_seconds=settings.login_rate_limit_window_seconds,
    name="login",
)

api_write_rate_limiter = RateLimiter(attempts=240, window_seconds=60, name="api-write")
