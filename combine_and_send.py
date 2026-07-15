from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from msa_chatbot import build_matches, load_customer_profiles


@dataclass(frozen=True)
class Notification:
    account: str
    contacts: list[str]
    msa_id: str
    subject: str
    customer_raw_path: Path
    raw_msa_path: Path
    matching_services: list[str]


def build_notifications() -> list[Notification]:
    customer_profiles = load_customer_profiles()
    notifications: list[Notification] = []

    for profile in customer_profiles.values():
        for match in build_matches(profile.company_id):
            notifications.append(
                Notification(
                    account=profile.company_name,
                    contacts=profile.contacts,
                    msa_id=match.msa_id,
                    subject=match.subject,
                    customer_raw_path=profile.raw_customer_path,
                    raw_msa_path=match.raw_msa_path,
                    matching_services=match.matching_services,
                )
            )

    return notifications


def pretend_send_notification(notification: Notification) -> None:
    services = ", ".join(notification.matching_services)
    recipients = ", ".join(notification.contacts) or "missing-contact@example.com"

    print("PRETEND EMAIL")
    print(f"  to: {recipients}")
    print(f"  subject: Relevant Google Cloud MSA update: {notification.subject}")
    print(f"  company: {notification.account}")
    print(f"  matched_services: {services}")
    print(f"  customer_raw_source: {notification.customer_raw_path}")
    print(f"  msa_to_send: {notification.raw_msa_path}")
    print()


def main() -> None:
    notifications = build_notifications()

    if not notifications:
        print("No customer service keywords matched cleaned MSA keywords.")
        return

    for notification in notifications:
        pretend_send_notification(notification)


if __name__ == "__main__":
    main()
