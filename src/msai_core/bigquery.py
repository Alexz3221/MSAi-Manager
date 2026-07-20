from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _setting(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise RuntimeError(f"Set {name} before using the BigQuery data source.")
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise RuntimeError(f"{name} contains unsupported characters: {value!r}")
    return value


def bigquery_settings() -> tuple[str, str, str, str]:
    project = _setting("BQ_PROJECT_ID", os.environ.get("GOOGLE_CLOUD_PROJECT"))
    dataset = _setting("BQ_DATASET", "msa_manager")
    customer_table = _setting("BQ_CUSTOMERS_TABLE", "customer_profiles")
    msa_table = _setting("BQ_MSA_UPDATES_TABLE", "msa_updates")
    return project, dataset, customer_table, msa_table


def _client():
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise RuntimeError(
            "BigQuery mode requires the google-cloud-bigquery package. "
            "Install dependencies with 'pip install -r requirements.txt'."
        ) from exc

    project, _, _, _ = bigquery_settings()
    return bigquery.Client(project=project)


def _plain_value(value: Any) -> Any:
    if hasattr(value, "items"):
        return {key: _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _query_records(query: str) -> list[dict[str, Any]]:
    rows = _client().query(query).result()
    return [_plain_value(row) for row in rows]


def load_customer_records() -> list[dict[str, Any]]:
    project, dataset, customer_table, _ = bigquery_settings()
    return _query_records(
        f"""
        WITH distinct_services AS (
          SELECT DISTINCT
            TRIM(project) AS project_name,
            TRIM(service) AS service
          FROM `{project}.{dataset}.{customer_table}`
          WHERE NULLIF(TRIM(project), '') IS NOT NULL
            AND NULLIF(TRIM(service), '') IS NOT NULL
        )
        SELECT
          project_name AS company_id,
          project_name AS company_name,
          ARRAY<STRING>[] AS contacts,
          CAST(NULL AS STRING) AS raw_customer_path,
          ARRAY_AGG(
            STRUCT(service AS name, ARRAY<STRING>[] AS aliases)
            ORDER BY service
          ) AS services
        FROM distinct_services
        GROUP BY project_name
        ORDER BY project_name
        """
    )


def load_msa_records() -> list[dict[str, Any]]:
    project, dataset, _, msa_table = bigquery_settings()
    return _query_records(
        f"""
        SELECT
          msa_id,
          raw_msa_path,
          format,
          sent_date,
          distribution_date,
          category,
          subject,
          headline,
          bug_id,
          launch_owner,
          requires_customer_action,
          cost_implications,
          effective_date,
          reminder_dates,
          affected_customers,
          affected_services,
          _match_scope
        FROM `{project}.{dataset}.{msa_table}`
        ORDER BY sent_date DESC, msa_id
        """
    )
