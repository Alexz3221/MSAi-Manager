from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from bigquery_data import bigquery_settings


ROOT = Path(__file__).parent
CUSTOMER_PROFILES_DIR = ROOT / "customer_data" / "customer_keywords_cleaned"
MSA_PROFILES_DIR = ROOT / "msa_data" / "msa_keywords_cleaned"


def read_records(directory: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8-sig"))
        for path in sorted(directory.glob("*.json"))
    ]


def normalized_msa_records() -> list[dict[str, Any]]:
    records = read_records(MSA_PROFILES_DIR)
    for record in records:
        if "sent_date" not in record and "date" in record:
            record["sent_date"] = record.pop("date")
    return records


def load_table(client, table_id: str, records: list[dict[str, Any]], replace: bool) -> None:
    from google.cloud import bigquery

    if not records:
        print(f"Skipped {table_id}: no local JSON records found.")
        return

    write_disposition = (
        bigquery.WriteDisposition.WRITE_TRUNCATE
        if replace
        else bigquery.WriteDisposition.WRITE_APPEND
    )
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition)
    job = client.load_table_from_json(records, table_id, job_config=job_config)
    job.result()
    print(f"Loaded {len(records)} rows into {table_id} ({write_disposition}).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load local customer and MSA JSON into existing BigQuery tables."
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace all table rows instead of appending. Use this for repeatable demo seeding.",
    )
    args = parser.parse_args()

    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise SystemExit(
            "Install dependencies first: pip install -r requirements.txt"
        ) from exc

    project, dataset, customer_table, msa_table = bigquery_settings()
    client = bigquery.Client(project=project)
    load_table(
        client,
        f"{project}.{dataset}.{customer_table}",
        read_records(CUSTOMER_PROFILES_DIR),
        args.replace,
    )
    load_table(
        client,
        f"{project}.{dataset}.{msa_table}",
        normalized_msa_records(),
        args.replace,
    )


if __name__ == "__main__":
    main()
