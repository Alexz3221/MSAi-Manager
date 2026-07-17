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


def customer_row(
    project_name: Any,
    service: Any,
    raw_uri: Any = None,
) -> dict[str, str | None] | None:
    project_value = str(project_name or "").strip()
    service_value = str(service or "").strip()
    raw_uri_value = str(raw_uri).strip() if raw_uri is not None else None
    if not project_value or not service_value:
        return None
    return {
        "project_name": project_value,
        "service": service_value,
        "raw_uri": raw_uri_value or None,
    }


def normalized_customer_records() -> list[dict[str, str | None]]:
    rows: dict[tuple[str, str, str | None], dict[str, str | None]] = {}

    for path in sorted(CUSTOMER_PROFILES_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        records = payload if isinstance(payload, list) else [payload]

        for record in records:
            if not isinstance(record, dict):
                continue

            services = record.get("services")
            if isinstance(services, list):
                project_name = record.get("company_id") or record.get("project_name")
                default_uri = record.get("raw_customer_path")
                for service_record in services:
                    if not isinstance(service_record, dict):
                        continue
                    row = customer_row(
                        project_name,
                        service_record.get("name"),
                        service_record.get("source") or default_uri,
                    )
                    if row:
                        rows[(row["project_name"], row["service"], row["raw_uri"])] = row
                continue

            row = customer_row(
                record.get("project_name") or record.get("project") or record.get("company_id"),
                record.get("service"),
                record.get("raw_uri"),
            )
            if row:
                rows[(row["project_name"], row["service"], row["raw_uri"])] = row

    pairs_with_uri = {
        (project_name, service)
        for project_name, service, raw_uri in rows
        if raw_uri is not None
    }
    return sorted(
        (
            row
            for (project_name, service, raw_uri), row in rows.items()
            if raw_uri is not None or (project_name, service) not in pairs_with_uri
        ),
        key=lambda row: (
            str(row["project_name"]),
            str(row["service"]),
            str(row["raw_uri"] or ""),
        ),
    )


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
        normalized_customer_records(),
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
