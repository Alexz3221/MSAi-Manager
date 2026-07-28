from __future__ import annotations
import json
import os
import urllib.request
import logging
import re
from functools import lru_cache

from google.cloud.sql.connector import Connector
from msai_core import matching
from msai_core.matching import MsaProfile, service_terms
from .customer_voice import to_customer_voice

LOGGER = logging.getLogger(__name__)


SLACK_WEBHOOK_RE = re.compile(
    r"^https://hooks\.slack\.com/services/T[\w]+/B[\w]+/[\w]+$"
)


def is_valid_slack_webhook(url: str) -> bool:
    return bool(SLACK_WEBHOOK_RE.match(url.strip()))


def mask_webhook(url: str) -> str:
    return f"…{url[-8:]}" if len(url) > 8 else "…"

_IN_MEMORY_WEBHOOKS: dict[str, str] = {}

_IN_MEMORY_NOTIFIED: set[tuple[str, str, str]] = set()

def check_table() -> None:
    if not CLOUD_SQL_PASSWORD:
        LOGGER.info("CLOUD_SQL_PASSWORD not set. Using in-memory store for Slack webhooks.")
        return
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_channels (
                company_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                webhook_url TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (company_id, channel_type)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_log (
                msa_id TEXT NOT NULL,
                company_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (msa_id, company_id, channel_type)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

def already_notified(msa_id: str, company_id: str, channel_type: str) -> bool:
    if not CLOUD_SQL_PASSWORD:
        return (msa_id, company_id, channel_type) in _IN_MEMORY_NOTIFIED
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM notification_log WHERE msa_id = %s AND company_id = %s AND channel_type = %s",
            (msa_id, company_id, channel_type),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def record_notified(msa_id: str, company_id: str, channel_type: str) -> None:
    if not CLOUD_SQL_PASSWORD:
        _IN_MEMORY_NOTIFIED.add((msa_id, company_id, channel_type))
        return
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO notification_log (msa_id, company_id, channel_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (msa_id, company_id, channel_type) DO NOTHING
            """,
            (msa_id, company_id, channel_type),
        )
        conn.commit()
    finally:
        conn.close()

def get_slack_webhook(company_id: str) -> str | None:
    if not CLOUD_SQL_PASSWORD:
        return _IN_MEMORY_WEBHOOKS.get(company_id)
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT webhook_url FROM company_channels "
            "WHERE company_id = %s AND channel_type = 'slack'",
            (company_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def upsert_slack_webhook(company_id: str, webhook_url: str) -> None:
    if not CLOUD_SQL_PASSWORD:
        _IN_MEMORY_WEBHOOKS[company_id] = webhook_url
        return
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO company_channels (company_id, channel_type, webhook_url, updated_at)
            VALUES (%s, 'slack', %s, now())
            ON CONFLICT (company_id, channel_type)
            DO UPDATE SET webhook_url = EXCLUDED.webhook_url, updated_at = now()
            """,
            (company_id, webhook_url),
        )
        conn.commit()
    finally:
        conn.close()


def delete_slack_webhook(company_id: str) -> None:
    if not CLOUD_SQL_PASSWORD:
        _IN_MEMORY_WEBHOOKS.pop(company_id, None)
        return
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM company_channels WHERE company_id = %s AND channel_type = 'slack'",
            (company_id,),
        )
        conn.commit()
    finally:
        conn.close()




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
    if not CLOUD_SQL_PASSWORD:
        return _IN_MEMORY_WEBHOOKS.copy()
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
        f"*{verdict}*{deadline}\n"
        f"Hello {company.company_name} team,\n"
        f"Here's what's changing and how it affects your *{services}* service\n"
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
    summary = to_customer_voice(matching.profile_summary(msa_profile, raw_text))
    actions = [
        to_customer_voice(a)
        for a in matching.action_items(matching.section_lines(raw_text, "WHAT YOU NEED TO DO"))
    ]
    companies = matching.load_customer_profiles()
    webhooks = get_all_slack_webhooks()  

    for company in companies.values():
        services = matching.matching_customer_services(company, msa_profile)
        if not services:
            continue
        webhook = webhooks.get(company.company_id)
        if webhook is None:
            continue
        if already_notified(msa_profile.msa_id, company.company_id, "slack"):
            continue
        try:
            send_slack_message(webhook, company, msa_profile, services, summary, actions)
            record_notified(msa_profile.msa_id, company.company_id, "slack")
        except Exception:
           LOGGER.exception(
           "Slack notify failed",
            extra={"event": "slack_notify_failed", "company_id": company.company_id},
           )
           continue
