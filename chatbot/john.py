from __future__ import annotations

import asyncio
import os
from typing import Any, Protocol

from . import query
from .matching import *
from .matching import __all__ as _matching_exports


PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "sprinternship-bld-2026")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
MODEL = os.environ.get("JOHN_MODEL", "gemini-3.5-flash")
PRINCIPAL = os.environ.get("JOHN_PRINCIPAL", "usagi@example.com")
USER_ID = os.environ.get("JOHN_USER_ID", "088")


class ToolContextLike(Protocol):
    state: dict[str, Any]


def find_msas_affecting_my_projects(
    tool_context: ToolContextLike,
    lookback_days: int = 90,
    product: str | None = None,
) -> dict[str, Any]:
    """Find published MSA notices matching projects available to the caller."""
    principal = tool_context.state.get("principal_email", PRINCIPAL)
    return query.find_msas(principal, lookback_days=lookback_days, product=product)


SYSTEM = """You help Google Cloud customers understand which MSA notices affect them.

Rules:
- Call find_msas_affecting_my_projects for any question about notices, deadlines,
  migrations, or what affects the user's projects. For greetings, thanks, or
  unrelated messages, just respond — do not call the tool.
- If the tool returns zero notices, say plainly that nothing matched. Do not fill
  the silence with general advice.
- Cite the msa_bug_id and the specific project IDs behind every claim.
- Lead with the soonest deadline, not the newest notice. Use days_left.
- Never state a deadline, migration target, or model version the tool did not return.
"""


def create_agent_app():
    """Build John's ADK app only when the conversational agent is requested."""
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

    from google.adk.agents import Agent
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=PROJECT_ID, location=LOCATION)
    agent = Agent(
        model=MODEL,
        name="msa_advisor",
        instruction=SYSTEM,
        tools=[find_msas_affecting_my_projects],
    )
    return agent_engines.AdkApp(agent=agent)


async def agent_main() -> None:
    app = None
    session_id = None
    print("msa advisor — 'quit' or ctrl-d to exit\n")
    while True:
        try:
            message = input("ask: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break

        if not message:
            continue
        if message.lower() in {"quit", "exit", "q"}:
            break

        try:
            if app is None:
                app = create_agent_app()
                session = await app.async_create_session(user_id=USER_ID)
                session_id = session["id"] if isinstance(session, dict) else session.id

            async for event in app.async_stream_query(
                user_id=USER_ID,
                session_id=session_id,
                message=message,
            ):
                for part in event.get("content", {}).get("parts", []):
                    if "function_call" in part:
                        function_call = part["function_call"]
                        print(
                            f"[tool] {function_call['name']}"
                            f"({function_call['args']})"
                        )
                    elif "text" in part:
                        print(f"\n{part['text']}\n")
        except Exception as exc:
            print(f"[error] {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    asyncio.run(agent_main())


__all__ = [
    *_matching_exports,
    "LOCATION",
    "MODEL",
    "PRINCIPAL",
    "PROJECT_ID",
    "SYSTEM",
    "USER_ID",
    "agent_main",
    "create_agent_app",
    "find_msas_affecting_my_projects",
]
