from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from google.adk.agents import Agent

from msai_core import matching   # was: from . import query


load_dotenv(Path(__file__).resolve().parents[3] / ".env")


PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "sprinternship-bld-2026")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
MODEL = os.environ.get("JOHN_MODEL", "gemini-3.5-flash")
USER_ID = os.environ.get("JOHN_USER_ID", "088")


class ToolContextLike(Protocol):
    state: dict[str, Any]


def _feeditem_to_dict(item) -> dict[str, Any]:
    """FeedItem -> JSON-safe dict. Path and date fields coerced to str."""
    d = dataclasses.asdict(item)
    return _stringify(d)


def _stringify(value):
    if isinstance(value, dict):
        return {k: _stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def list_customers(tool_context: ToolContextLike, name_query: str = "") -> dict[str, Any]:
    """Lists customer companies known to the system, optionally filtered by name.

    Use this to check whether a company exists before looking up its notices, or
    when the user asks who is in the system. An empty result means no company
    matched the query -- the company is unknown, not that it has no notices.

    Args:
        name_query: Partial company name, e.g. 'Endeavour'. Empty lists all.
    """
    try:
        profiles = matching.load_customer_profiles()
        hits = [
            {"company_id": p.company_id, "company_name": p.company_name,
             "services": sorted(p.services)}
            for p in profiles.values()
            if not name_query
            or matching.find_company(name_query, {p.company_id: p}) is not None
        ]
        return {"customers": hits, "count": len(hits)}
    except Exception as exc:
        print(f"[tool error] list_customers: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}", "customers": []}


def find_msas_for_customer(
    tool_context: ToolContextLike,
    company: str,
    service: str | None = None,
    requires_action: bool | None = None,
) -> dict[str, Any]:
    """Finds MSA notices affecting one customer, soonest effective_date first.

    Use for any question about which notices affect a named customer, what they
    must migrate, or upcoming deadlines. An empty notices list with found=true
    means nothing matched. found=false means the company is unknown -- say so,
    do not report notices for anyone else. If the result has an 'error' field,
    the lookup failed; say so rather than reporting nothing matched.

    Args:
        company: Company name, e.g. 'Endeavour Group Limited'. Partial names work.
        service: Optional service filter, e.g. 'cloud sql'.
        requires_action: True for action-required only, False for informational only.
    """
    try:
        companies = matching.load_customer_profiles()
        resolved = matching.find_company(company, companies)
        if resolved is None:
            return {"found": False, "company": company, "notices": [], "count": 0}

        feed = matching.build_feed(
            company_query=company,
            service_query=service,
            requires_action=requires_action,
        )
        notices = [_feeditem_to_dict(i) for i in feed]
        return {"found": True, "company": companies[resolved].company_name,
                "notices": notices, "count": len(notices)}
    except Exception as exc:
        print(f"[tool error] find_msas_for_customer: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}", "notices": []}


def who_is_affected_by(
    tool_context: ToolContextLike,
    msa_id: str = "",
    service: str | None = None,
) -> dict[str, Any]:
    """Lists which customers are affected by a notice -- the inverse lookup.

    Use for 'which companies are affected by X' questions. Give an msa_id for one
    notice, or a service to span all notices touching that service.

    Args:
        msa_id: A specific notice, e.g. 'msa_04'. Omit to search across notices.
        service: Optional service filter, e.g. 'cloud run'.
    """
    try:
        feed = matching.build_feed(service_query=service)
        items = [i for i in feed if not msa_id or i.msa_id == msa_id]
        return {"notices": [
            {"msa_id": i.msa_id, "subject": i.subject,
             "effective_date": str(i.effective_date) if i.effective_date else None,
             "companies": [
                 {"company_name": c.company_name,
                  "matching_services": c.matching_services}
                 for c in i.impacted_companies]}
            for i in items
        ], "count": len(items)}
    except Exception as exc:
        print(f"[tool error] who_is_affected_by: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}", "notices": []}


SYSTEM = """You help Google account teams understand which MSA notices affect their customers.

Tool use:
- For "which notices affect <company>": call find_msas_for_customer.
- For "which companies are affected by <notice/service>": call who_is_affected_by.
- To check whether a company exists or list customers: call list_customers.
- For greetings, thanks, or unrelated messages: just reply, no tools.
- Never answer from background knowledge about GCP deprecations. Always use a tool.

Answering:
- If find_msas_for_customer returns found=false, say you don't have that company
  on file. Do NOT list notices for any other company, and do NOT append unrelated
  notices. Stop there.
- If found=true with an empty notices list, say plainly that no current notices
  match that customer.
- Cite msa_id for every claim, and name the matching_services that caused each match.
- Lead with the soonest effective_date.
- A match is inferred from service-name overlap, not confirmed resource usage. Say
  "this may affect you because you use X", not "you must migrate X".
- requires_customer_action=false is informational -- do not call it a deadline.
- Never state a date, version, or migration target the tool did not return.
- If a tool returns an 'error' field, say the lookup failed. Do not say nothing matched.
"""


def create_root_agent() -> Agent:
    """Create the ADK agent shared by the CLI and Cloud Run API server."""
    return Agent(
        model=MODEL,
        name="msa_advisor",
        description="Explains which MSA notices affect a customer.",
        instruction=SYSTEM,
        tools=[list_customers, find_msas_for_customer, who_is_affected_by],
    )


root_agent = create_root_agent()


def create_agent_app():
    """Build John's ADK app only when the conversational agent is requested."""
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=PROJECT_ID, location=LOCATION)
    return agent_engines.AdkApp(agent=root_agent)


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
                        fc = part["function_call"]
                        print(f"[tool] {fc['name']}({fc['args']})")
                    elif "text" in part:
                        print(f"\n{part['text']}\n")
        except Exception as exc:
            print(f"[error] {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    asyncio.run(agent_main())


__all__ = [
    "LOCATION", "MODEL", "PROJECT_ID", "SYSTEM", "USER_ID",
    "agent_main", "create_agent_app", "create_root_agent",
    "list_customers", "find_msas_for_customer", "who_is_affected_by",
    "root_agent",
]
