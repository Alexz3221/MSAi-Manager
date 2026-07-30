from __future__ import annotations

def format_notification_message(company, msa_profile, matching_services, summary, actions) -> str:
    verdict = "🔴 ACTION REQUIRED" if msa_profile.requires_customer_action else "🔵 NEW MSA"
    services = ", ".join(matching_services)
    deadline = f" — due {msa_profile.effective_date}" if msa_profile.effective_date else ""
    action_lines = "\n".join(f"• {a}" for a in actions) if actions else ""
    url = "https://msai-manager-1053168925742.europe-west1.run.app/"
    link_display = f"<{url}|Ask John in-app>"
    return (
        f"*{verdict}*{deadline}\n"
        f"Hello {company.company_name} team,\n"
        f"Here's what's changing and how it affects your *{services}* service\n"
        f"{summary}\n"
        f"{action_lines}\n"
        f"{link_display} for a personalized breakdown of what this means for you."
    )