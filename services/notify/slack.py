from __future__ import annotations
import json
import os
import urllib.request
import logging
from functools import lru_cache

from google.cloud.sql.connector import Connector
from msai_core import matching
from msai_core.matching import MsaProfile, service_terms

LOGGER = logging.getLogger(__name__)


def profile_dict_to_msa_profile(profile: dict) -> MsaProfile:
    affected_services = dict(
        service_terms(service) for service in profile.get("affected_services", [])
    )
    return MsaProfile(
        msa_id=profile["msa_id"],
        affected_services=affected_services,
        raw_msa_path=profile["raw_msa_path"],
        subject=profile.get("subject"),
        headline=profile.get("headline"),
        date=profile.get("sent_date"),
        distribution_date=profile.get("distribution_date"),
        effective_date=profile.get("effective_date"),
        requires_customer_action=bool(profile.get("requires_customer_action", False)),
    )

#Cloud SQL connection settings
CLOUD_SQL_CONNECTION_NAME = os.environ.get(
    "CLOUD_SQL_CONNECTION_NAME",
    "sprinternship-bld-2026:europe-west1:msai-company-channels",
)
CLOUD_SQL_DB = os.environ.get("CLOUD_SQL_DB", "msai_manager")
CLOUD_SQL_USER = os.environ.get("CLOUD_SQL_USER", "postgres")
CLOUD_SQL_PASSWORD = os.environ.get("CLOUD_SQL_PASSWORD")  


@lru_cache(maxsize=1)
def _connector() -> Connector:
    return Connector()


def _get_connection():
    if not CLOUD_SQL_PASSWORD:
        raise RuntimeError("CLOUD_SQL_PASSWORD environment variable is not set.")
    return _connector().connect(
        CLOUD_SQL_CONNECTION_NAME,
        "pg8000",
        user=CLOUD_SQL_USER,
        password=CLOUD_SQL_PASSWORD,
        db=CLOUD_SQL_DB,
    )


#webhook helper 
def get_all_slack_webhooks() -> dict[str, str]:
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT company_id, webhook_url FROM company_channels WHERE channel_type = 'slack'"
        )
        return dict(cursor.fetchall())
    finally:
        conn.close()



def format_for_slack(company, msa_profile, matching_services, summary, actions) -> str:
    verdict = "🔴 ACTION REQUIRED" if msa_profile.requires_customer_action else "🔵 NEW MSA"
    services = ", ".join(matching_services)
    deadline = f" — due {msa_profile.effective_date}" if msa_profile.effective_date else ""
    action_lines = "\n".join(f"• {a}" for a in actions) if actions else ""
    url = f"https://msai-manager-1053168925742.europe-west1.run.app/"
    link_display = f"<{url}|Ask John in-app>"
    return (
        f"Hello {company.company_name} team,\n"
        f"*{verdict}*{deadline}\n"
        f"This affects your *{services}*\n"
        f"{summary}\n"
        f"{action_lines}\n"
        f"{link_display} for a personalized breakdown of what this means for you."
    )



def send_slack_message(webhook_url, company, msa_profile, matching_services, summary, actions) -> None:
    text = format_for_slack(company, msa_profile, matching_services, summary, actions)
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack webhook returned {resp.status}")



def notify_channels(msa_profile) -> None:
    raw_text = matching.read_text(msa_profile.raw_msa_path)
    summary = matching.profile_summary(msa_profile, raw_text)
    actions = matching.action_items(matching.section_lines(raw_text, "WHAT YOU NEED TO DO"))
    companies = matching.load_customer_profiles()
    webhooks = get_all_slack_webhooks()  

    for company in companies.values():
        services = matching.matching_customer_services(company, msa_profile)
        if not services:
            continue
        webhook = webhooks.get(company.company_id)
        if webhook is None:
            continue
        try:
            send_slack_message(webhook, company, msa_profile, services, summary, actions)
        except Exception:
           LOGGER.exception(
           "Slack notify failed",
            extra={"event": "slack_notify_failed", "company_id": company.company_id},
           )
           continue