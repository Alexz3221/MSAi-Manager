from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parent
CUSTOMER_RAW_DIR = ROOT / "customer_data" / "raw"
CUSTOMER_KEYWORDS_DIR = ROOT / "customer_data" / "customer_keywords_cleaned"
MSA_KEYWORDS_DIR = ROOT / "msa_data" / "msa_keywords_cleaned"
RAW_MSA_DIR = ROOT / "msa_data" / "raw"


@dataclass(frozen=True)
class Notification:
    account: str
    msa_id: str
    subject: str
    customer_raw_path: Path
    raw_msa_path: Path
    matching_services: list[str]


def read_keywords(csv_path: Path) -> set[str]:
    """Read one keyword per row from the first column of a CSV."""
    keywords: set[str] = set()

    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        for row in reader:
            if row and row[0].strip():
                keywords.add(row[0].strip().casefold())

    return keywords


def load_keyword_files(folder: Path) -> dict[str, set[str]]:
    return {
        csv_path.stem: read_keywords(csv_path)
        for csv_path in sorted(folder.glob("*.csv"))
    }


def raw_msa_id_for_keyword_file(keyword_file_id: str) -> str:
    if keyword_file_id.endswith("_keywords"):
        return keyword_file_id.removesuffix("_keywords")
    return keyword_file_id


def raw_msa_path_for(keyword_file_id: str) -> Path:
    return RAW_MSA_DIR / f"{raw_msa_id_for_keyword_file(keyword_file_id)}.txt"


def raw_customer_path_for(account_name: str) -> Path:
    return CUSTOMER_RAW_DIR / f"{account_name}.txt"


def read_msa_subject(raw_msa_path: Path) -> str:
    if not raw_msa_path.exists():
        return "Unknown subject"

    with raw_msa_path.open(encoding="utf-8-sig", errors="replace") as file:
        for line in file:
            if line.startswith("Subject:"):
                return line.removeprefix("Subject:").strip()

    return "Unknown subject"


def build_notifications() -> list[Notification]:
    customer_accounts = load_keyword_files(CUSTOMER_KEYWORDS_DIR)
    cleaned_msa_keywords = load_keyword_files(MSA_KEYWORDS_DIR)
    notifications: list[Notification] = []

    for account_name, account_services in customer_accounts.items():
        for keyword_file_id, msa_keywords in cleaned_msa_keywords.items():
            matching_services = sorted(account_services & msa_keywords)
            if not matching_services:
                continue

            raw_msa_path = raw_msa_path_for(keyword_file_id)
            notifications.append(
                Notification(
                    account=account_name,
                    msa_id=raw_msa_id_for_keyword_file(keyword_file_id),
                    subject=read_msa_subject(raw_msa_path),
                    customer_raw_path=raw_customer_path_for(account_name),
                    raw_msa_path=raw_msa_path,
                    matching_services=matching_services,
                )
            )

    return notifications


def pretend_send_notification(notification: Notification) -> None:
    services = ", ".join(notification.matching_services)

    print("PRETEND EMAIL")
    print(f"  to: legal-contact+{notification.account}@example.com")
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
