from __future__ import annotations

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app


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
        self.assertIn("test-get-trace", logs)
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
        self.assertIn("test-post-trace", logs)


if __name__ == "__main__":
    unittest.main()
