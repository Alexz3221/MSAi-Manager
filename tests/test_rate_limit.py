from __future__ import annotations

import unittest

from services.web.rate_limit import JohnRateLimiter


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class JohnRateLimiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.limiter = JohnRateLimiter(
            per_client_limit=2,
            per_client_window_seconds=60,
            global_limit=3,
            global_window_seconds=120,
            clock=self.clock,
        )

    def test_enforces_client_and_global_limits(self) -> None:
        self.assertTrue(self.limiter.check("client-a").allowed)
        self.assertTrue(self.limiter.check("client-a").allowed)

        client_denied = self.limiter.check("client-a")
        self.assertFalse(client_denied.allowed)
        self.assertEqual(client_denied.reason, "client")
        self.assertEqual(client_denied.retry_after_seconds, 60)

        self.assertTrue(self.limiter.check("client-b").allowed)
        global_denied = self.limiter.check("client-c")
        self.assertFalse(global_denied.allowed)
        self.assertEqual(global_denied.reason, "global")
        self.assertEqual(global_denied.retry_after_seconds, 120)

    def test_requests_are_allowed_after_windows_expire(self) -> None:
        self.assertTrue(self.limiter.check("client-a").allowed)
        self.assertTrue(self.limiter.check("client-a").allowed)

        self.clock.now = 61
        self.assertTrue(self.limiter.check("client-a").allowed)

        self.clock.now = 121
        self.assertTrue(self.limiter.check("client-b").allowed)

    def test_expired_client_buckets_are_removed(self) -> None:
        self.assertTrue(self.limiter.check("expired-client").allowed)

        self.clock.now = 61
        self.assertTrue(self.limiter.check("current-client").allowed)

        self.assertNotIn("expired-client", self.limiter._client_requests)


if __name__ == "__main__":
    unittest.main()
