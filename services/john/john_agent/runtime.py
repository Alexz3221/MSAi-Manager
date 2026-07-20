from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from .agent import PRINCIPAL, create_agent_app


class JohnRuntime:
    """Run John's async ADK app behind the synchronous web server."""

    def __init__(
        self,
        app_factory: Callable[[], Any] = create_agent_app,
        timeout_seconds: int = 180,
    ) -> None:
        self._app_factory = app_factory
        self._timeout_seconds = timeout_seconds
        self._startup_lock = threading.Lock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._app: Any = None
        self._session_lock: asyncio.Lock | None = None
        self._sessions: set[tuple[str, str]] = set()

    def _start(self) -> None:
        with self._startup_lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._ready.clear()

            def run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                self._ready.set()
                loop.run_forever()

            self._thread = threading.Thread(
                target=run_loop,
                name="john-adk-runtime",
                daemon=True,
            )
            self._thread.start()

        if not self._ready.wait(timeout=5):
            raise RuntimeError("John's async runtime did not start.")

    @staticmethod
    def _session_id(session: Any) -> str:
        if isinstance(session, dict):
            return str(session["id"])
        return str(session.id)

    async def _chat(
        self,
        message: str,
        user_id: str,
        session_id: str | None,
    ) -> dict[str, object]:
        if self._app is None:
            self._app = self._app_factory()
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()

        requested_session = session_id
        async with self._session_lock:
            session_key = (user_id, requested_session or "")
            if not requested_session or session_key not in self._sessions:
                session = await self._app.async_create_session(
                    user_id=user_id,
                    session_id=requested_session,
                    state={"principal_email": PRINCIPAL},
                )
                session_id = self._session_id(session)
                self._sessions.add((user_id, session_id))

        text_parts: list[str] = []
        tools: list[str] = []
        async for event in self._app.async_stream_query(
            user_id=user_id,
            session_id=session_id,
            message=message,
        ):
            for part in event.get("content", {}).get("parts", []):
                function_call = part.get("function_call")
                if function_call:
                    tool_name = str(function_call.get("name", ""))
                    if tool_name and tool_name not in tools:
                        tools.append(tool_name)
                text = str(part.get("text", "")).strip()
                if text:
                    text_parts.append(text)

        reply = "\n\n".join(text_parts).strip()
        if not reply:
            reply = "John did not return a text response. Please try again."

        return {
            "session_id": session_id,
            "reply": reply,
            "tools": tools,
        }

    def chat(
        self,
        message: str,
        user_id: str,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self._start()
        if self._loop is None:
            raise RuntimeError("John's async runtime is unavailable.")

        future = asyncio.run_coroutine_threadsafe(
            self._chat(message, user_id, session_id),
            self._loop,
        )
        try:
            return future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError("John's response timed out.") from exc


__all__ = ["JohnRuntime"]
