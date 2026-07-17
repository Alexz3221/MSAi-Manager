from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    reason: str | None = None


class JohnRateLimiter:
    """Thread-safe in-memory client and global sliding-window limiter."""

    def __init__(
        self,
        *,
        per_client_limit: int,
        per_client_window_seconds: int,
        global_limit: int,
        global_window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value in {
            "per_client_limit": per_client_limit,
            "per_client_window_seconds": per_client_window_seconds,
            "global_limit": global_limit,
            "global_window_seconds": global_window_seconds,
        }.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive.")

        self.per_client_limit = per_client_limit
        self.per_client_window_seconds = per_client_window_seconds
        self.global_limit = global_limit
        self.global_window_seconds = global_window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._global_requests: deque[float] = deque()
        self._client_requests: dict[str, deque[float]] = {}

    @staticmethod
    def _prune(requests: deque[float], cutoff: float) -> None:
        while requests and requests[0] <= cutoff:
            requests.popleft()

    @staticmethod
    def _retry_after(requests: deque[float], window: int, now: float) -> int:
        return max(1, math.ceil(requests[0] + window - now))

    def check(self, client_key: str) -> RateLimitDecision:
        now = self._clock()

        with self._lock:
            self._prune(
                self._global_requests,
                now - self.global_window_seconds,
            )

            client_cutoff = now - self.per_client_window_seconds
            for existing_key, requests in list(self._client_requests.items()):
                self._prune(
                    requests,
                    client_cutoff,
                )
                if not requests:
                    del self._client_requests[existing_key]

            client_requests = self._client_requests.get(client_key)

            if client_requests and len(client_requests) >= self.per_client_limit:
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=self._retry_after(
                        client_requests,
                        self.per_client_window_seconds,
                        now,
                    ),
                    reason="client",
                )

            if len(self._global_requests) >= self.global_limit:
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=self._retry_after(
                        self._global_requests,
                        self.global_window_seconds,
                        now,
                    ),
                    reason="global",
                )

            if client_requests is None:
                client_requests = deque()
                self._client_requests[client_key] = client_requests

            client_requests.append(now)
            self._global_requests.append(now)
            return RateLimitDecision(allowed=True)


__all__ = ["JohnRateLimiter", "RateLimitDecision"]
