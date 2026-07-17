from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent
CUSTOMER_RAW_DIR = ROOT / "customer_data" / "raw"
CUSTOMER_PROFILES_DIR = ROOT / "customer_data" / "customer_keywords_cleaned"
MSA_PROFILES_DIR = ROOT / "msa_data" / "msa_keywords_cleaned"
RAW_MSA_DIR = ROOT / "msa_data" / "raw"


@dataclass(frozen=True)
class CustomerProfile:
    company_id: str
    company_name: str
    contacts: list[str]
    services: dict[str, set[str]]
    raw_customer_path: Path


@dataclass(frozen=True)
class MsaProfile:
    msa_id: str
    affected_services: dict[str, set[str]]
    raw_msa_path: Path
    subject: str | None
    headline: str | None
    date: str | None
    effective_date: str | None
    requires_customer_action: bool


@dataclass(frozen=True)
class MsaMatch:
    msa_id: str
    subject: str
    date: str
    effective_date: str | None
    requires_customer_action: bool
    matching_services: list[str]
    summary: str
    actions: list[str]
    raw_msa_path: Path


@dataclass(frozen=True)
class FeedImpact:
    company_id: str
    company_name: str
    contacts: list[str]
    matching_services: list[str]


@dataclass(frozen=True)
class FeedItem:
    msa_id: str
    subject: str
    date: str
    effective_date: str | None
    requires_customer_action: bool
    affected_services: list[str]
    impacted_companies: list[FeedImpact]
    summary: str
    actions: list[str]
    raw_msa_path: Path


def normalize_name(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def normalize_term(value: str) -> str:
    return " ".join(value.strip().casefold().replace("-", " ").split())


def display_name(value: str) -> str:
    return value.replace("_", " ").title()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def flat_customer_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_services: dict[str, set[str]] = {}

    for row in rows:
        project_name = str(
            row.get("project_name") or row.get("project") or ""
        ).strip()
        service = str(row.get("service") or "").strip()
        if not project_name or not service:
            continue
        grouped_services.setdefault(project_name, set()).add(service)

    return [
        {
            "company_id": project_name,
            "company_name": display_name(project_name),
            "contacts": [],
            "raw_customer_path": None,
            "services": [
                {"name": service, "aliases": []}
                for service in sorted(services)
            ],
        }
        for project_name, services in sorted(grouped_services.items())
    ]


def local_customer_records() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    flat_rows: list[dict[str, Any]] = []

    for path in sorted(CUSTOMER_PROFILES_DIR.glob("*.json")):
        payload = read_json(path)
        if isinstance(payload, list):
            flat_rows.extend(row for row in payload if isinstance(row, dict))
        elif isinstance(payload, dict):
            if "company_id" in payload:
                profiles.append(payload)
            else:
                flat_rows.append(payload)
        else:
            raise ValueError(f"Unsupported customer JSON root in {path}")

    return [*profiles, *flat_customer_profiles(flat_rows)]


def service_terms(service: dict[str, Any]) -> tuple[str, set[str]]:
    name = str(service["name"])
    aliases = [str(alias) for alias in service.get("aliases", [])]
    terms = {normalize_term(name), *(normalize_term(alias) for alias in aliases)}
    return normalize_term(name), {term for term in terms if term}


def data_source() -> str:
    source = os.environ.get("DATA_SOURCE", "local").strip().casefold()
    if source not in {"local", "bigquery"}:
        raise RuntimeError("DATA_SOURCE must be either 'local' or 'bigquery'.")
    return source


def resolve_data_path(value: Any, default_directory: Path, default_name: str) -> Path:
    candidate = Path(str(value or default_name))
    if candidate.is_absolute():
        return candidate

    root_relative = ROOT / candidate
    if root_relative.exists() or candidate.parent != Path("."):
        return root_relative
    return default_directory / candidate.name


def customer_records() -> list[dict[str, Any]]:
    if data_source() == "bigquery":
        from bigquery_data import load_customer_records

        return load_customer_records()
    return local_customer_records()


def msa_records() -> list[dict[str, Any]]:
    if data_source() == "bigquery":
        from bigquery_data import load_msa_records

        return load_msa_records()
    return [read_json(path) for path in sorted(MSA_PROFILES_DIR.glob("*.json"))]


def load_customer_profiles() -> dict[str, CustomerProfile]:
    profiles: dict[str, CustomerProfile] = {}

    for payload in customer_records():
        company_id = normalize_name(str(payload["company_id"]))
        raw_customer_path = resolve_data_path(
            payload.get("raw_customer_path"),
            CUSTOMER_RAW_DIR,
            f"{company_id}.txt",
        )
        services = dict(service_terms(service) for service in payload.get("services", []))
        profiles[company_id] = CustomerProfile(
            company_id=company_id,
            company_name=str(payload.get("company_name") or display_name(company_id)),
            contacts=[str(contact) for contact in payload.get("contacts", [])],
            services=services,
            raw_customer_path=raw_customer_path,
        )

    return profiles


def load_msa_profiles() -> dict[str, MsaProfile]:
    profiles: dict[str, MsaProfile] = {}

    for payload in msa_records():
        msa_id = str(payload["msa_id"])
        raw_msa_path = resolve_data_path(
            payload.get("raw_msa_path"),
            RAW_MSA_DIR,
            f"{msa_id}.txt",
        )
        affected_services = dict(
            service_terms(service) for service in payload.get("affected_services", [])
        )
        profiles[msa_id] = MsaProfile(
            msa_id=msa_id,
            affected_services=affected_services,
            raw_msa_path=raw_msa_path,
            subject=payload.get("subject"),
            headline=payload.get("headline"),
            date=payload.get("sent_date") or payload.get("date"),
            effective_date=payload.get("effective_date"),
            requires_customer_action=bool(payload.get("requires_customer_action", False)),
        )

    return profiles


def read_text(path: Path) -> str:
    if not path.exists():
        return ""

    return path.read_text(encoding="utf-8-sig", errors="replace")


def extract_prefixed_line(text: str, prefix: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return fallback


def section_lines(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    collected: list[str] = []
    in_section = False

    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            in_section = True
            continue

        if in_section and stripped.isupper() and stripped:
            break

        if in_section and stripped:
            collected.append(stripped)

    return collected


def first_paragraph(lines: list[str], max_lines: int = 3) -> str:
    paragraph: list[str] = []

    for line in lines:
        if line.startswith(("1.", "2.", "3.", "*")):
            continue
        paragraph.append(line)
        if len(paragraph) >= max_lines:
            break

    return " ".join(paragraph) if paragraph else "No summary available."


def raw_summary(raw_text: str) -> str:
    know_section = first_paragraph(section_lines(raw_text, "WHAT YOU NEED TO KNOW"))
    if know_section != "No summary available.":
        return know_section

    for line in raw_text.splitlines():
        if line.startswith("TLDR:"):
            return line.removeprefix("TLDR:").strip()

    return "No summary available."


def profile_summary(msa_profile: MsaProfile, raw_text: str) -> str:
    summary = raw_summary(raw_text)
    if summary != "No summary available.":
        return summary
    return msa_profile.headline or summary


def action_items(lines: list[str], max_items: int = 3) -> list[str]:
    actions = [
        line
        for line in lines
        if line.startswith(("1.", "2.", "3.", "4.", "5."))
    ]
    return actions[:max_items]


def find_company(
    company_query: str,
    companies: dict[str, CustomerProfile],
) -> str | None:
    wanted = normalize_name(company_query)

    if wanted in companies:
        return wanted

    for company_id, profile in companies.items():
        candidates = {
            company_id,
            normalize_name(profile.company_name),
        }
        if any(wanted in candidate or candidate in wanted for candidate in candidates):
            return company_id

    return None


def matching_customer_services(
    customer_profile: CustomerProfile,
    msa_profile: MsaProfile,
) -> list[str]:
    matches: list[str] = []

    for service_name, customer_terms in customer_profile.services.items():
        for msa_terms in msa_profile.affected_services.values():
            if customer_terms & msa_terms:
                matches.append(service_name)
                break

    return sorted(matches)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def matches_service_filter(msa_profile: MsaProfile, service_query: str | None) -> bool:
    if not service_query:
        return True

    wanted = normalize_term(service_query)
    for terms in msa_profile.affected_services.values():
        if wanted in terms or any(wanted in term or term in wanted for term in terms):
            return True

    return False


def matches_effective_date_filter(
    msa_profile: MsaProfile,
    effective_from: str | None,
    effective_to: str | None,
) -> bool:
    effective_date = parse_iso_date(msa_profile.effective_date)
    if effective_date is None:
        return not effective_from and not effective_to

    from_date = parse_iso_date(effective_from)
    to_date = parse_iso_date(effective_to)

    if from_date and effective_date < from_date:
        return False
    if to_date and effective_date > to_date:
        return False
    return True


def build_matches(company_name: str) -> list[MsaMatch]:
    companies = load_customer_profiles()
    customer_profile = companies[company_name]
    cleaned_msa_profiles = load_msa_profiles()
    matches: list[MsaMatch] = []

    for msa_profile in cleaned_msa_profiles.values():
        matching_services = matching_customer_services(customer_profile, msa_profile)
        if not matching_services:
            continue

        raw_text = read_text(msa_profile.raw_msa_path)
        matches.append(
            MsaMatch(
                msa_id=msa_profile.msa_id,
                subject=msa_profile.subject
                or extract_prefixed_line(raw_text, "Subject:", "Unknown subject"),
                date=msa_profile.date
                or extract_prefixed_line(raw_text, "Date:", "Unknown date"),
                effective_date=msa_profile.effective_date,
                requires_customer_action=msa_profile.requires_customer_action,
                matching_services=matching_services,
                summary=profile_summary(msa_profile, raw_text),
                actions=action_items(section_lines(raw_text, "WHAT YOU NEED TO DO")),
                raw_msa_path=msa_profile.raw_msa_path,
            )
        )

    return matches


def build_feed(
    company_query: str | None = None,
    service_query: str | None = None,
    requires_action: bool | None = None,
    effective_from: str | None = None,
    effective_to: str | None = None,
) -> list[FeedItem]:
    companies = load_customer_profiles()
    msa_profiles = load_msa_profiles()
    company_filter = find_company(company_query, companies) if company_query else None
    feed: list[FeedItem] = []

    if company_query and company_filter is None:
        return feed

    filtered_companies = (
        {company_filter: companies[company_filter]}
        if company_filter
        else companies
    )

    for msa_profile in msa_profiles.values():
        if requires_action is not None and msa_profile.requires_customer_action != requires_action:
            continue
        if not matches_service_filter(msa_profile, service_query):
            continue
        if not matches_effective_date_filter(msa_profile, effective_from, effective_to):
            continue

        impacts: list[FeedImpact] = []
        for profile in filtered_companies.values():
            matching_services = matching_customer_services(profile, msa_profile)
            if matching_services:
                impacts.append(
                    FeedImpact(
                        company_id=profile.company_id,
                        company_name=profile.company_name,
                        contacts=profile.contacts,
                        matching_services=matching_services,
                    )
                )

        if not impacts:
            continue

        raw_text = read_text(msa_profile.raw_msa_path)
        feed.append(
            FeedItem(
                msa_id=msa_profile.msa_id,
                subject=msa_profile.subject
                or extract_prefixed_line(raw_text, "Subject:", "Unknown subject"),
                date=msa_profile.date
                or extract_prefixed_line(raw_text, "Date:", "Unknown date"),
                effective_date=msa_profile.effective_date,
                requires_customer_action=msa_profile.requires_customer_action,
                affected_services=sorted(msa_profile.affected_services),
                impacted_companies=impacts,
                summary=profile_summary(msa_profile, raw_text),
                actions=action_items(section_lines(raw_text, "WHAT YOU NEED TO DO")),
                raw_msa_path=msa_profile.raw_msa_path,
            )
        )

    return sorted(
        feed,
        key=lambda item: (
            parse_iso_date(item.effective_date) or date.max,
            item.msa_id,
        ),
    )


def print_company_answer(company_query: str) -> None:
    companies = load_customer_profiles()
    company_name = find_company(company_query, companies)

    if company_name is None:
        print(f"I could not find a cleaned customer profile for '{company_query}'.")
        print("Available companies:")
        for available_company in companies.values():
            print(f"  - {available_company.company_name}")
        return

    profile = companies[company_name]
    matches = build_matches(company_name)
    services = ", ".join(sorted(profile.services))

    print(f"Company: {profile.company_name}")
    print(f"Detected GCP services: {services}")
    print()

    if not matches:
        print("No relevant MSA updates found for this company's detected services.")
        return

    print(f"Relevant MSA updates found: {len(matches)}")
    for index, match in enumerate(matches, start=1):
        matched_services = ", ".join(match.matching_services)
        print()
        print(f"{index}. {match.subject}")
        print(f"   Date: {match.date}")
        if match.effective_date:
            print(f"   Effective date: {match.effective_date}")
        print(f"   Customer action required: {match.requires_customer_action}")
        print(f"   Matched services: {matched_services}")
        print(f"   Summary: {match.summary}")
        if match.actions:
            print("   Actions:")
            for action in match.actions:
                print(f"   - {action}")
        print(f"   Raw MSA: {match.raw_msa_path}")


def interactive_chat() -> None:
    print("MSA relevance chatbot")
    print("Enter a company name to see relevant Google Cloud MSA updates.")
    print("Type 'companies' to list examples, or 'quit' to exit.")
    print()

    while True:
        company_query = input("Company> ").strip()
        if not company_query:
            continue

        if company_query.casefold() in {"quit", "exit"}:
            print("Bye")
            return

        if company_query.casefold() == "companies":
            companies = load_customer_profiles()
            for profile in companies.values():
                print(f"  - {profile.company_name}")
            print()
            continue

        print_company_answer(company_query)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Find MSA updates relevant to a company.")
    parser.add_argument("--company", help="Company name to look up without opening chat mode.")
    args = parser.parse_args()

    if args.company:
        print_company_answer(args.company)
        return

    interactive_chat()


if __name__ == "__main__":
    main()
