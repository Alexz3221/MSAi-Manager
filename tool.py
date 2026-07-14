
# Starter module for project tooling.

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).parent
CUSTOMER_ACCOUNTS_DIR = ROOT / "customer accounts"
MSA_ADDENDUMS_DIR = ROOT / "MSA addendums"


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


def find_accounts_with_addendums() -> list[dict[str, object]]:
    customer_accounts = load_keyword_files(CUSTOMER_ACCOUNTS_DIR)
    msa_addendums = load_keyword_files(MSA_ADDENDUMS_DIR)
    flagged_accounts: list[dict[str, object]] = []

    for account_name, account_keywords in customer_accounts.items():
        matches = []

        for addendum_name, addendum_keywords in msa_addendums.items():
            shared_keywords = sorted(account_keywords & addendum_keywords)
            if shared_keywords:
                matches.append(
                    {
                        "addendum": addendum_name,
                        "matching_keywords": shared_keywords,
                    }
                )

        if matches:
            flagged_accounts.append(
                {
                    "account": account_name,
                    "status": "FLAGGED",
                    "matches": matches,
                }
            )

    return flagged_accounts


def main() -> None:
    flagged_accounts = find_accounts_with_addendums()

    if not flagged_accounts:
        print("No customer accounts matched pending MSA addendums.")
        return

    for account in flagged_accounts:
        print(f"{account['status']}: {account['account']}")
        for match in account["matches"]:
            keywords = ", ".join(match["matching_keywords"])
            print(f"  - {match['addendum']} matched on: {keywords}")


if __name__ == "__main__":
    main()

