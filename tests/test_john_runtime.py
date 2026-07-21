from __future__ import annotations

import unittest

from services.john.john_agent.runtime import JohnRuntime


class FakeJohnApp:
    def __init__(self) -> None:
        self.created_sessions: list[tuple[str, str | None, dict[str, object]]] = []

    async def async_create_session(self, *, user_id, session_id=None, state=None):
        self.created_sessions.append((user_id, session_id, state or {}))
        return {"id": session_id or "generated-session"}

    async def async_stream_query(self, *, user_id, session_id, message):
        yield {
            "content": {
                "parts": [
                    {
                        "function_call": {
                            "name": "find_msas_for_customer",
                            "args": {},
                        }
                    }
                ]
            }
        }
        yield {"content": {"parts": [{"text": f"Answer for {message}"}]}}


class JohnRuntimeTests(unittest.TestCase):
    def test_chat_creates_and_reuses_an_adk_session(self) -> None:
        app = FakeJohnApp()
        runtime = JohnRuntime(app_factory=lambda: app, timeout_seconds=2)

        first = runtime.chat("first question", "demo-user")
        second = runtime.chat(
            "follow-up",
            "demo-user",
            session_id=str(first["session_id"]),
        )

        self.assertEqual(first["session_id"], "generated-session")
        self.assertEqual(first["reply"], "Answer for first question")
        self.assertEqual(first["tools"], ["find_msas_for_customer"])
        self.assertEqual(second["reply"], "Answer for follow-up")
        self.assertEqual(len(app.created_sessions), 1)


if __name__ == "__main__":
    unittest.main()
