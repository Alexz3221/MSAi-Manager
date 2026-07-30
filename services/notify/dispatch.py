from __future__ import annotations
import logging

from msai_core import matching
from msai_core.matching import MsaProfile, service_terms
from .customer_voice import to_customer_voice
from .message import format_notification_message
from .channels import already_notified, record_notified, get_all_webhooks_for_channel
from . import slack, gchat

LOGGER = logging.getLogger(__name__)

_SENDERS = {
    "slack": slack.send_slack_message,
    "gchat": gchat.send_gchat_message,
}


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


def notify_channels(msa_profile) -> None:
    raw_text = matching.read_text(msa_profile.raw_msa_path)
    summary = to_customer_voice(matching.profile_summary(msa_profile, raw_text))
    actions = [
        to_customer_voice(a)
        for a in matching.action_items(matching.section_lines(raw_text, "WHAT YOU NEED TO DO"))
    ]
    companies = matching.load_customer_profiles()
    webhooks_by_channel = {
        channel_type: get_all_webhooks_for_channel(channel_type)
        for channel_type in _SENDERS
    }

    for company in companies.values():
        services = matching.matching_customer_services(company, msa_profile)
        if not services:
            continue

        text = format_notification_message(company, msa_profile, services, summary, actions)

        for channel_type, sender in _SENDERS.items():
            webhook = webhooks_by_channel[channel_type].get(company.company_id)
            if webhook is None:
                continue
            if already_notified(msa_profile.msa_id, company.company_id, channel_type):
                continue
            try:
                sender(webhook, text)
                record_notified(msa_profile.msa_id, company.company_id, channel_type)
            except Exception:
                LOGGER.exception(
                    "%s notify failed",
                    channel_type,
                    extra={"event": f"{channel_type}_notify_failed", "company_id": company.company_id},
                )
                continue