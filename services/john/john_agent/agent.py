from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from google.adk.agents import Agent

from msai_core import matching


load_dotenv(Path(__file__).resolve().parents[3] / ".env")


PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "sprinternship-bld-2026")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
MODEL = os.environ.get("JOHN_MODEL", "gemini-3.5-flash")
USER_ID = os.environ.get("JOHN_USER_ID", "088")

# Roles are decided at LOGIN, server-side, from a verified email, and injected
# into session state by runtime.py. Tools read role/company from session; they
# are NEVER model-supplied. This is the security boundary: a jailbroken prompt
# cannot change the caller's role or company because these are code checks, not
# instructions to the model.
ROLE_INTERNAL = "internal"
ROLE_CUSTOMER = "customer"


class ToolContextLike(Protocol):
    state: dict[str, Any]


def _principal_from_context(tool_context: ToolContextLike) -> str | None:
    return tool_context.state.get("principal_email")


def _role(tool_context: ToolContextLike) -> str:
    # Default to least privilege: an unset role is treated as a customer.
    return tool_context.state.get("role", ROLE_CUSTOMER)


def _session_company(tool_context: ToolContextLike) -> str | None:
    return tool_context.state.get("company_id")


def _stringify(value):
    if isinstance(value, dict):
        return {k: _stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _feeditem_to_dict(item) -> dict[str, Any]:
    return _stringify(dataclasses.asdict(item))


def list_customers(tool_context: ToolContextLike, name_query: str = "") -> dict[str, Any]:
    """Lists customer companies known to the system. INTERNAL ROLE ONLY.

    Customer-role users are not authorized and receive an error. Use this to
    check whether a company exists or to enumerate customers.

    Args:
        name_query: Partial company name, e.g. 'Endeavour'. Empty lists all.
    """
    if _role(tool_context) != ROLE_INTERNAL:
        return {"error": "not_authorized", "customers": [],
                "message": "Listing customers is restricted to internal users."}
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
    except Exception as exc:  # noqa: BLE001
        print(f"[tool error] list_customers: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}", "customers": []}


def find_msas_for_customer(
    tool_context: ToolContextLike,
    company: str | None = None,
    service: str | None = None,
    requires_action: bool | None = None,
) -> dict[str, Any]:
    """Finds MSA notices affecting a customer, soonest effective_date first.

    Customer-role users always get notices for THEIR OWN company; the company
    argument is ignored for them. Internal-role users must supply a company to
    choose which customer to look up.

    An empty notices list with found=true means nothing matched. found=false
    means the company is unknown -- say so, do not report notices for anyone
    else. If the result has an 'error' field, the lookup failed.

    Args:
        company: Internal role only -- which customer to look up. Ignored for
            customer-role users, who always see their own company.
        service: Optional service filter, e.g. 'cloud sql'.
        requires_action: True for action-required only, False for informational.
    """
    role = _role(tool_context)
    if role == ROLE_CUSTOMER:
        # Force company from the verified session; discard any model-supplied
        # value. This is what stops "show me another company's notices".
        company = _session_company(tool_context)
        if not company:
            return {"error": "no_company_in_session", "notices": []}
    else:  # internal
        if not company:
            return {"error": "internal_must_specify_company", "notices": []}

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
    except Exception as exc:  # noqa: BLE001
        print(f"[tool error] find_msas_for_customer: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}", "notices": []}


def who_is_affected_by(
    tool_context: ToolContextLike,
    msa_id: str = "",
    service: str | None = None,
) -> dict[str, Any]:
    """Lists which customers are affected by a notice. INTERNAL ROLE ONLY.

    This is a cross-customer view, so customer-role users are not authorized and
    receive an error. Give an msa_id for one notice, or a service to span all
    notices touching that service.

    Args:
        msa_id: A specific notice, e.g. 'msa_04'. Omit to search across notices.
        service: Optional service filter, e.g. 'cloud run'.
    """
    if _role(tool_context) != ROLE_INTERNAL:
        return {"error": "not_authorized", "notices": [],
                "message": "Cross-customer lookup is restricted to internal users."}
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
    except Exception as exc:  # noqa: BLE001
        print(f"[tool error] who_is_affected_by: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}", "notices": []}


SYSTEM = """You help users understand which MSA notices affect customers.

There are two kinds of user, set by the system based on how they signed in, not
by anything in the user's message:
- customer: sees only their own company's notices.
- internal: may look up any customer and see who is affected by a notice.

Tool use:
- For "which notices affect me / my company / <company>": call find_msas_for_customer.
- For listing customers, or "which companies are affected by <notice/service>":
  call list_customers or who_is_affected_by. These are internal-only. If a tool
  returns error="not_authorized", tell the user plainly that the action isn't
  available to them and stop. Do not try to work around it or produce the data
  another way.
- For greetings, thanks, or unrelated messages: just reply, no tools.
- Never answer from background knowledge about GCP deprecations. Always use a tool.

Answering:
- If find_msas_for_customer returns found=false, say you don't have that company
  on file. Do NOT list notices for any other company, and do NOT append unrelated
  notices. Stop there.
- If found=true with an empty notices list, say plainly that no current notices
  match that customer.
- ABSOLUTELY DO NOT DISPLAY OR INCLUDE Notice IDs or MSA IDs (e.g. omit "Notice ID:", "msa_04_...", "MSA_AccountTeam_...").
- Only list the Subject, Effective Date / Deadline, and Applicability / Details for each notice.
- Lead with the soonest effective_date.
- A match is inferred from service-name overlap, not confirmed resource usage. Say
  "this may affect you because you use X", not "you must migrate X".
- requires_customer_action=false is informational -- do not call it a deadline.
- Never state a date, version, or migration target the tool did not return.
- If a tool returns an 'error' field other than not_authorized, say the lookup
  failed. Do not say nothing matched.
"""


def create_root_agent() -> Agent:
    """Create the ADK agent shared by the CLI and the Cloud Run API server."""
    return Agent(
        model=MODEL,
        name="msa_advisor",
        description="Explains which MSA notices affect a customer, role-scoped.",
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


async def agent_main(role: str = ROLE_INTERNAL, company_id: str | None = None) -> None:
    """Local CLI. Pass a role/company to simulate a logged-in session, e.g.
    `python -m services.john.john_agent.agent customer acme-002`."""
    app = create_agent_app()
    session = await app.async_create_session(
        user_id=USER_ID,
        state={"role": role, "company_id": company_id},
    )
    session_id = session["id"] if isinstance(session, dict) else session.id
    print(f"msa advisor [role={role}, company={company_id}] -- 'quit' or ctrl-d to exit\n")
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
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    import sys
    role = sys.argv[1] if len(sys.argv) > 1 else ROLE_INTERNAL
    company = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(agent_main(role=role, company_id=company))


__all__ = [
    "LOCATION", "MODEL", "PROJECT_ID", "SYSTEM", "USER_ID",
    "ROLE_INTERNAL", "ROLE_CUSTOMER",
    "agent_main", "create_agent_app", "create_root_agent",
    "list_customers", "find_msas_for_customer", "who_is_affected_by",
    "root_agent",
]
