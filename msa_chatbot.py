from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parent
CUSTOMER_KEYWORDS_DIR = ROOT / "customer_data" / "customer_keywords_cleaned"
MSA_KEYWORDS_DIR = ROOT / "msa_data" / "msa_keywords_cleaned"
RAW_MSA_DIR = ROOT / "msa_data" / "raw"


@dataclass(frozen=True)
class MsaMatch:
    msa_id: str
    subject: str
    date: str
    matching_services: list[str]
    summary: str
    actions: list[str]
    raw_msa_path: Path


def normalize_name(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def display_name(value: str) -> str:
    return value.replace("_", " ").title()


def read_keywords(csv_path: Path) -> set[str]:
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


def cleaned_keyword_id_to_msa_id(keyword_file_id: str) -> str:
    if keyword_file_id.endswith("_keywords"):
        return keyword_file_id.removesuffix("_keywords")
    return keyword_file_id


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


def action_items(lines: list[str], max_items: int = 3) -> list[str]:
    actions = [
        line
        for line in lines
        if line.startswith(("1.", "2.", "3.", "4.", "5."))
    ]
    return actions[:max_items]


def find_company(company_query: str, companies: dict[str, set[str]]) -> str | None:
    wanted = normalize_name(company_query)

    if wanted in companies:
        return wanted

    for company_name in companies:
        if wanted in company_name or company_name in wanted:
            return company_name

    return None


def build_matches(company_name: str) -> list[MsaMatch]:
    companies = load_keyword_files(CUSTOMER_KEYWORDS_DIR)
    customer_services = companies[company_name]
    cleaned_msa_keywords = load_keyword_files(MSA_KEYWORDS_DIR)
    matches: list[MsaMatch] = []

    for keyword_file_id, msa_keywords in cleaned_msa_keywords.items():
        matching_services = sorted(customer_services & msa_keywords)
        if not matching_services:
            continue

        msa_id = cleaned_keyword_id_to_msa_id(keyword_file_id)
        raw_msa_path = RAW_MSA_DIR / f"{msa_id}.txt"
        raw_text = read_text(raw_msa_path)
        matches.append(
            MsaMatch(
                msa_id=msa_id,
                subject=extract_prefixed_line(raw_text, "Subject:", "Unknown subject"),
                date=extract_prefixed_line(raw_text, "Date:", "Unknown date"),
                matching_services=matching_services,
                summary=first_paragraph(section_lines(raw_text, "WHAT YOU NEED TO KNOW")),
                actions=action_items(section_lines(raw_text, "WHAT YOU NEED TO DO")),
                raw_msa_path=raw_msa_path,
            )
        )

    return matches


def print_company_answer(company_query: str) -> None:
    companies = load_keyword_files(CUSTOMER_KEYWORDS_DIR)
    company_name = find_company(company_query, companies)

    if company_name is None:
        print(f"I could not find a cleaned customer profile for '{company_query}'.")
        print("Available companies:")
        for available_company in companies:
            print(f"  - {display_name(available_company)}")
        return

    matches = build_matches(company_name)
    services = ", ".join(sorted(companies[company_name]))

    print(f"Company: {display_name(company_name)}")
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
            companies = load_keyword_files(CUSTOMER_KEYWORDS_DIR)
            for company_name in companies:
                print(f"  - {display_name(company_name)}")
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
