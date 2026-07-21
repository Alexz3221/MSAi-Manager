from __future__ import annotations

import os
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
QueryParameter = tuple[str, str, Any]


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


def queue_settings() -> tuple[str, str, str]:
    project, _, _, _ = bigquery_settings()
    dataset = _setting("BQ_QUEUE_DATASET", "msa_dataset")
    table = _setting("BQ_DAILY_QUEUE_TABLE", "msa_daily_queue")
    return project, dataset, table


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


def _query_job_config(parameters: list[QueryParameter]):
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(name, parameter_type, value)
            for name, parameter_type, value in parameters
        ]
    )


def _query_records(
    query: str,
    parameters: list[QueryParameter] | None = None,
) -> list[dict[str, Any]]:
    client = _client()
    job = (
        client.query(query, job_config=_query_job_config(parameters))
        if parameters
        else client.query(query)
    )
    rows = job.result()
    return [_plain_value(row) for row in rows]


def _execute_dml(query: str, parameters: list[QueryParameter]) -> int:
    job = _client().query(query, job_config=_query_job_config(parameters))
    job.result()
    return int(job.num_dml_affected_rows or 0)


@lru_cache(maxsize=None)
def _queue_partition_field(project: str, dataset: str, table: str) -> str | None:
    """Return the time-partition field, or _PARTITIONTIME for ingestion time."""
    metadata = _client().get_table(f"{project}.{dataset}.{table}")
    time_partitioning = metadata.time_partitioning
    if time_partitioning is None:
        return None

    field = time_partitioning.field
    if field is None:
        return "_PARTITIONTIME"
    if not IDENTIFIER_PATTERN.fullmatch(field):
        raise RuntimeError(f"Queue partition field is not a safe identifier: {field!r}")
    return field


def _queue_partition_filter(
    alias: str,
    field: str | None,
) -> str:
    if field is None:
        raise RuntimeError(
            "msa_daily_queue must be time-partitioned so the daily job does "
            "not scan the entire table."
        )
    if field == "_PARTITIONTIME":
        return f"{alias}._PARTITIONDATE = @as_of"
    return (
        f"{alias}.`{field}` >= TIMESTAMP(@as_of) "
        f"AND {alias}.`{field}` < "
        f"TIMESTAMP(DATE_ADD(@as_of, INTERVAL 1 DAY))"
    )


def _queue_status(alias: str) -> str:
    return f"LOWER(TRIM({alias}.status))"


def _queue_available_filter(alias: str) -> str:
    return f"{_queue_status(alias)} IN ('pending', 'queued', 'failed')"


def load_customer_records() -> list[dict[str, Any]]:
    project, dataset, customer_table, _ = bigquery_settings()
    return _query_records(
        f"""
        SELECT
          client_id AS company_id,
          account   AS company_name,
          ARRAY<STRING>[] AS contacts,
          CAST(NULL AS STRING) AS raw_customer_path,
          ARRAY(
            SELECT AS STRUCT
              TRIM(svc) AS name,
              ARRAY<STRING>[] AS aliases
            FROM UNNEST(active_services) AS svc
            WHERE NULLIF(TRIM(svc), '') IS NOT NULL
          ) AS services
        FROM `{project}.{dataset}.{customer_table}`
        WHERE NULLIF(TRIM(client_id), '') IS NOT NULL
        ORDER BY account
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


def load_pending_queue_records(as_of: date) -> list[dict[str, Any]]:
    """Load eligible deliveries from exactly one daily queue partition."""
    project, msa_dataset, _, msa_table = bigquery_settings()
    _, queue_dataset, queue_table = queue_settings()
    partition_field = _queue_partition_field(project, queue_dataset, queue_table)
    partition_filter = _queue_partition_filter("q", partition_field)
    available_filter = _queue_available_filter("q")

    return _query_records(
        f"""
        WITH pending_queue AS (
          SELECT
            TRIM(q.msa_id) AS msa_id,
            TRIM(q.client_id) AS client_id,
            MAX(q.update_details) AS update_details
          FROM `{project}.{queue_dataset}.{queue_table}` AS q
          WHERE {partition_filter}
            AND {available_filter}
          GROUP BY msa_id, client_id
        ),
        latest_msa_updates AS (
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
          FROM `{project}.{msa_dataset}.{msa_table}`
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY msa_id
            ORDER BY sent_date DESC, distribution_date DESC, subject DESC
          ) = 1
        )
        SELECT
          q.msa_id,
          q.client_id,
          q.update_details,
          m.msa_id IS NOT NULL AS msa_exists,
          m.raw_msa_path,
          m.format,
          m.sent_date,
          m.distribution_date,
          m.category,
          m.subject,
          m.headline,
          m.bug_id,
          m.launch_owner,
          m.requires_customer_action,
          m.cost_implications,
          m.effective_date,
          m.reminder_dates,
          m.affected_customers,
          m.affected_services,
          m._match_scope
        FROM pending_queue AS q
        LEFT JOIN latest_msa_updates AS m
          ON m.msa_id = q.msa_id
        ORDER BY q.client_id, q.msa_id
        """,
        [("as_of", "DATE", as_of)],
    )


def claim_queue_record(
    msa_id: str,
    client_id: str,
    as_of: date,
) -> int:
    """Claim an eligible delivery without changing its partition timestamp."""
    project, queue_dataset, queue_table = queue_settings()
    partition_field = _queue_partition_field(project, queue_dataset, queue_table)
    partition_filter = _queue_partition_filter("q", partition_field)
    available_filter = _queue_available_filter("q")

    return _execute_dml(
        f"""
        UPDATE `{project}.{queue_dataset}.{queue_table}` AS q
        SET status = 'processing'
        WHERE {partition_filter}
          AND TRIM(q.msa_id) = @msa_id
          AND TRIM(q.client_id) = @client_id
          AND {available_filter}
        """,
        [
            ("as_of", "DATE", as_of),
            ("msa_id", "STRING", msa_id),
            ("client_id", "STRING", client_id),
        ],
    )


def mark_queue_record_sent(
    msa_id: str,
    client_id: str,
    as_of: date,
) -> int:
    """Mark claimed duplicates for one delivery as sent after SMTP succeeds."""
    project, queue_dataset, queue_table = queue_settings()
    partition_field = _queue_partition_field(project, queue_dataset, queue_table)
    partition_filter = _queue_partition_filter("q", partition_field)

    return _execute_dml(
        f"""
        UPDATE `{project}.{queue_dataset}.{queue_table}` AS q
        SET status = 'sent'
        WHERE {partition_filter}
          AND TRIM(q.msa_id) = @msa_id
          AND TRIM(q.client_id) = @client_id
          AND LOWER(TRIM(q.status)) = 'processing'
        """,
        [
            ("as_of", "DATE", as_of),
            ("msa_id", "STRING", msa_id),
            ("client_id", "STRING", client_id),
        ],
    )


def mark_queue_record_failed(
    msa_id: str,
    client_id: str,
    as_of: date,
) -> int:
    """Release a claimed delivery for retry after an SMTP failure."""
    project, queue_dataset, queue_table = queue_settings()
    partition_field = _queue_partition_field(project, queue_dataset, queue_table)
    partition_filter = _queue_partition_filter("q", partition_field)

    return _execute_dml(
        f"""
        UPDATE `{project}.{queue_dataset}.{queue_table}` AS q
        SET status = 'failed'
        WHERE {partition_filter}
          AND TRIM(q.msa_id) = @msa_id
          AND TRIM(q.client_id) = @client_id
          AND LOWER(TRIM(q.status)) = 'processing'
        """,
        [
            ("as_of", "DATE", as_of),
            ("msa_id", "STRING", msa_id),
            ("client_id", "STRING", client_id),
        ],
    )
