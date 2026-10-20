"""One rate limiter shared by every worker that calls the Claude API.

- Token budget: input tokens used in the last minute (from each response's `usage`) plus what
  in-flight requests are expected to use must stay under the budget.
- Cool-down: a 429 pauses every worker until its retry-after has passed, not just the one hit.
- Jitter: every wait gets a random extra, so workers do not all wake on the same tick.
- At most `max_in_flight` requests at once.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from typing import Callable

WINDOW_SECONDS = 60.0
MAX_JITTER_SECONDS = 2.0
MIN_WAIT_SECONDS = 0.5
INITIAL_ESTIMATE_TOKENS = 40_000  # web search results make requests large
ESTIMATE_WEIGHT = 0.3  # how fast the per-request estimate follows real usage


class RateLimiter:
    def __init__(self, tokens_per_minute: int, max_in_flight: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, jitter: Callable[[], float] | None = None) -> None:
        self._budget = tokens_per_minute
        self._max_in_flight = max_in_flight
        self._clock, self._sleep = clock, sleep
        self._jitter = jitter or (lambda: random.uniform(0, MAX_JITTER_SECONDS))
        self._lock = threading.Lock()
        self._used: deque[tuple[float, int]] = deque()  # (time, input tokens) of finished requests
        self._reserved: dict[int, int] = {}  # ticket -> expected tokens of in-flight requests
        self._next_ticket = 0
        self._cooldown_until = 0.0
        self.estimate = INITIAL_ESTIMATE_TOKENS

    def _wait_needed(self, now: float, need: int) -> float:
        """Seconds until a request of `need` tokens may start (0 = now). Caller holds the lock."""
        while self._used and self._used[0][0] <= now - WINDOW_SECONDS:
            self._used.popleft()
        if now < self._cooldown_until:
            return self._cooldown_until - now
        if len(self._reserved) >= self._max_in_flight:
            return MIN_WAIT_SECONDS
        in_window = sum(tokens for _t, tokens in self._used) + sum(self._reserved.values())
        if in_window + need <= self._budget or (not self._used and not self._reserved):
            return 0.0
        oldest = self._used[0][0] + WINDOW_SECONDS - now if self._used else MIN_WAIT_SECONDS
        return max(oldest, MIN_WAIT_SECONDS)

    def acquire(self) -> int:
        """Block until a request may start; returns a ticket for release()."""
        while True:
            with self._lock:
                need = min(int(self.estimate), self._budget)
                wait = self._wait_needed(self._clock(), need)
                if wait == 0.0:
                    ticket, self._next_ticket = self._next_ticket, self._next_ticket + 1
                    self._reserved[ticket] = need
                    return ticket
            self._sleep(wait + self._jitter())

    def release(self, ticket: int, input_tokens: int) -> None:
        """Record what the request really used (0 if it was rejected)."""
        with self._lock:
            self._reserved.pop(ticket, None)
            if input_tokens > 0:
                self._used.append((self._clock(), input_tokens))
                self.estimate = (1 - ESTIMATE_WEIGHT) * self.estimate + ESTIMATE_WEIGHT * input_tokens

    def cool_down(self, seconds: float) -> None:
        """Pause every worker for `seconds` (after a 429)."""
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, self._clock() + seconds)
