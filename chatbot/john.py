import asyncio, os

os.environ["GOOGLE_CLOUD_PROJECT"] = "sprinternship-bld-2026"
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

from google.adk.agents import Agent
from google.adk.tools.tool_context import ToolContext
from vertexai import agent_engines

import query

# The signed-in user. In prod this comes from a verified IAP assertion and is
# written into session state server-side. Hardcoded here for local dev only.
PRINCIPAL = "usagi@example.com"


def find_msas_affecting_my_projects(
    tool_context: ToolContext,
    lookback_days: int = 90,
    product: str | None = None,
) -> dict:
    """Finds published MSA notices that match resources in the caller's own projects.

    Use this for any question about which notices affect the user, what they need
    to migrate, or what deadlines are coming up. Results are already restricted to
    projects the user has access to. An empty list is a valid answer meaning
    nothing affects them.

    Args:
        lookback_days: How far back to look for published notices. Defaults to 90.
        product: Optional exact product filter, e.g. 'Cloud SQL'. Omit to search all.
    """
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

agent = Agent(
    model="gemini-3.5-flash",
    name="msa_advisor",
    instruction=SYSTEM,
    tools=[find_msas_affecting_my_projects],
)

app = agent_engines.AdkApp(agent=agent)

USER_ID = "088"

async def main():
    session = await app.async_create_session(user_id=USER_ID)
    session_id = session["id"] if isinstance(session, dict) else session.id

    print("msa advisor — 'quit' or ctrl-d to exit\n")
    while True:
        try:
            msg = input("ask: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break

        if not msg:
            continue
        if msg.lower() in {"quit", "exit", "q"}:
            break

        try:
            async for event in app.async_stream_query(
                user_id=USER_ID,
                session_id=session_id,
                message=msg,
            ):
                for part in event.get("content", {}).get("parts", []):
                    if "function_call" in part:
                        fc = part["function_call"]
                        print(f"[tool] {fc['name']}({fc['args']})")
                    elif "text" in part:
                        print(f"\n{part['text']}\n")
        except Exception as e:
            print(f"[error] {type(e).__name__}: {e}\n")

asyncio.run(main())
