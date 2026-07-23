from __future__ import annotations

import json
import logging
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from services.web import app
from services.web.rate_limit import RateLimitDecision


class RequestLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_get_exception_is_logged_and_returns_generic_503(self) -> None:
        with (
            patch.object(app, "companies_payload", side_effect=RuntimeError("query detail")),
            self.assertLogs(app.LOGGER.name, level="ERROR") as captured,
        ):
            with self.assertRaises(HTTPError) as raised:
                urlopen(
                    Request(
                        f"{self.base_url}/api/companies",
                        headers={"X-Cloud-Trace-Context": "test-get-trace"},
                    ),
                    timeout=5,
                )

        self.assertEqual(raised.exception.code, 503)
        payload = json.loads(raised.exception.read())
        self.assertEqual(payload, {"error": "Service unavailable"})
        logs = "\n".join(captured.output)
        self.assertIn("query detail", logs)
        self.assertEqual(captured.records[0].trace, "test-get-trace")
        self.assertEqual(captured.records[0].path, "/api/companies")
        self.assertNotIn("query detail", json.dumps(payload))

    def test_post_exception_is_logged_and_returns_generic_500(self) -> None:
        request = Request(
            f"{self.base_url}/",
            data=b"not-json",
            headers={
                "Content-Type": "application/json",
                "X-Cloud-Trace-Context": "test-post-trace",
            },
            method="POST",
        )

        with self.assertLogs(app.LOGGER.name, level="ERROR") as captured:
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)

        self.assertEqual(raised.exception.code, 500)
        payload = json.loads(raised.exception.read())
        self.assertEqual(payload, {"error": "Failed to process Pub/Sub message"})
        logs = "\n".join(captured.output)
        self.assertIn("JSONDecodeError", logs)
        self.assertEqual(captured.records[0].trace, "test-post-trace")
        self.assertEqual(captured.records[0].path, "/")

    def test_john_endpoint_returns_chat_payload(self) -> None:
        request = Request(
            f"{self.base_url}/api/john",
            data=json.dumps(
                {
                    "message": "What affects me?",
                    "user_id": "browser-user",
                    "session_id": "existing-session",
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-For": "203.0.113.10, 10.0.0.1",
            },
            method="POST",
        )
        expected = {
            "session_id": "existing-session",
            "reply": "One notice matches.",
            "tools": ["find_msas_for_customer"],
        }

        with (
            patch.object(
                app.JOHN_RATE_LIMITER,
                "check",
                return_value=RateLimitDecision(allowed=True),
            ) as rate_limit,
            patch.object(app.JOHN_RUNTIME, "chat", return_value=expected) as chat,
        ):
            response = urlopen(request, timeout=5)

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), expected)
        rate_limit.assert_called_once_with("203.0.113.10")
        chat.assert_called_once_with(
            message="What affects me?",
            user_id="browser-user",
            session_id="existing-session",
        )

    def test_john_endpoint_rejects_an_empty_message(self) -> None:
        request = Request(
            f"{self.base_url}/api/john",
            data=b'{"message":""}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)

        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(
            json.loads(raised.exception.read()),
            {"error": "Message is required."},
        )

    def test_john_endpoint_returns_retry_after_when_rate_limited(self) -> None:
        request = Request(
            f"{self.base_url}/api/john",
            data=b'{"message":"Hello"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        decision = RateLimitDecision(
            allowed=False,
            retry_after_seconds=45,
            reason="global",
        )

        with (
            patch.object(app.JOHN_RATE_LIMITER, "check", return_value=decision),
            patch.object(app.JOHN_RUNTIME, "chat") as chat,
            self.assertLogs(app.LOGGER.name, level="WARNING") as captured,
        ):
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)

        self.assertEqual(raised.exception.code, 429)
        self.assertEqual(raised.exception.headers["Retry-After"], "45")
        self.assertEqual(
            json.loads(raised.exception.read()),
            {
                "error": "John is receiving too many requests. Try again shortly.",
                "retry_after_seconds": 45,
            },
        )
        warning = captured.records[0]
        self.assertEqual(warning.event, "john_rate_limited")
        self.assertEqual(warning.rate_limit_reason, "global")
        self.assertEqual(warning.retry_after_seconds, 45)
        chat.assert_not_called()

    def test_john_endpoint_is_blocked_when_disabled(self) -> None:
        request = Request(
            f"{self.base_url}/api/john",
            data=b'{"message":"Hello"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with (
            patch.object(app, "JOHN_ENABLED", False),
            patch.object(app.JOHN_RATE_LIMITER, "check") as rate_limit,
            patch.object(app.JOHN_RUNTIME, "chat") as chat,
        ):
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)

        self.assertEqual(raised.exception.code, 503)
        self.assertEqual(
            json.loads(raised.exception.read()),
            {"error": "John is currently disabled."},
        )
        rate_limit.assert_not_called()
        chat.assert_not_called()

    def test_home_marks_john_offline_when_disabled(self) -> None:
        with patch.object(app, "JOHN_ENABLED", False):
            page = app.html_page()

        self.assertIn("window.JOHN_ENABLED = false;", page)
        john_js = (app.STATIC_DIR / "john.js").read_text(encoding = "utf-8")
        self.assertIn('johnTab.textContent = "John (offline)";', john_js)

    def test_json_log_formatter_outputs_cloud_logging_fields(self) -> None:
        record = logging.makeLogRecord(
            {
                "name": "test.logger",
                "levelno": logging.WARNING,
                "levelname": "WARNING",
                "msg": "Hello logging",
                "args": (),
                "event": "unit_test",
                "trace": "trace-id",
            }
        )

        payload = json.loads(app.JsonLogFormatter().format(record))

        self.assertEqual(payload["severity"], "WARNING")
        self.assertEqual(payload["message"], "Hello logging")
        self.assertEqual(payload["logger"], "test.logger")
        self.assertEqual(payload["event"], "unit_test")
        self.assertEqual(payload["trace"], "trace-id")


if __name__ == "__main__":
    unittest.main()
