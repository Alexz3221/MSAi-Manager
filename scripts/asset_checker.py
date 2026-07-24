import json
import logging
import os
import re
import uuid

from google.cloud import bigquery, storage

LOGGER = logging.getLogger(__name__)

DATASET_ID = os.environ.get("BQ_DATASET", "msa_manager")
TABLE_ID = os.environ.get("BQ_CUSTOMERS_TABLE", "customer_profiles")
STAGING_TABLE_ID = os.environ.get(
    "BQ_CUSTOMERS_STAGING_TABLE",
    "customer_profiles_staging",
)


def bigquery_client() -> bigquery.Client:
    project = os.environ.get("BQ_PROJECT_ID") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT"
    )
    return bigquery.Client(project=project) if project else bigquery.Client()


def read_gcs_file(bucket_name: str, file_path: str) -> str:
    # Downloads text content directly from GCS into memory
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_path)

    return blob.download_as_text()


def transform_txt_to_dict(text_content: str) -> dict:
    account_match = re.search(r"Account:\s*(.+)", text_content)
    client_id_match = re.search(r"Client ID:\s*(.+)", text_content)

    services = re.findall(r"^\s*-\s*(.+)$", text_content, re.MULTILINE)

    return {
        "account": account_match.group(1).strip() if account_match else None,
        "client_id": (
            client_id_match.group(1).strip() if client_id_match else None
        ),
        "active_services": [s.strip() for s in services],
    }


def merge_via_staging(
    dataset_id: str,
    target_table: str,
    staging_table: str,
    record: dict,
    *,
    client: bigquery.Client | None = None,
) -> None:
    """Replace one customer profile through an isolated batch staging table."""
    if not record.get("account") or not record.get("client_id"):
        raise ValueError("Customer profiles require non-empty account and client_id.")

    bq_client = client or bigquery_client()
    target_ref = f"{bq_client.project}.{dataset_id}.{target_table}"
    staging_ref = (
        f"{bq_client.project}.{dataset_id}.{staging_table}_{uuid.uuid4().hex}"
    )
    target_schema = bq_client.get_table(target_ref).schema

    load_config = bigquery.LoadJobConfig(
        schema=target_schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    try:
        LOGGER.info(
            "Loading incoming customer profile into staging table",
            extra={
                "event": "customer_profile_staging_load_started",
                "staging_table": staging_ref,
            },
        )
        load_job = bq_client.load_table_from_json(
            [record], staging_ref, job_config=load_config
        )
        load_job.result()

        replace_query = f"""
        BEGIN TRANSACTION;

        DELETE FROM `{target_ref}` AS target
        WHERE EXISTS (
          SELECT 1
          FROM `{staging_ref}` AS staged
          WHERE LOWER(TRIM(target.client_id)) = LOWER(TRIM(staged.client_id))
             OR LOWER(TRIM(target.account)) = LOWER(TRIM(staged.account))
        );

        INSERT INTO `{target_ref}` (account, client_id, active_services)
        SELECT account, client_id, active_services
        FROM `{staging_ref}`;

        COMMIT TRANSACTION;
        """

        LOGGER.info(
            "Replacing customer profile from staging data",
            extra={
                "event": "customer_profile_merge_started",
                "target_table": target_ref,
            },
        )
        bq_client.query(replace_query).result()
    finally:
        try:
            bq_client.delete_table(staging_ref, not_found_ok=True)
        except Exception:
            LOGGER.warning(
                "Could not remove customer profile staging table",
                extra={
                    "event": "customer_profile_staging_cleanup_failed",
                    "staging_table": staging_ref,
                },
                exc_info=True,
            )

    LOGGER.info(
        "Customer profile merged",
        extra={
            "event": "customer_profile_merged",
            "target_table": target_ref,
        },
    )


def main():
    # Read .txt file from GCS
    print(f"Reading file gs://{BUCKET_NAME}/{FILE_PATH}...")
    raw_text = read_gcs_file(BUCKET_NAME, FILE_PATH)

    # Convert text JSON
    json_record = transform_txt_to_dict(raw_text)
    print("Parsed JSON record:")
    print(json.dumps(json_record, indent=2))

    # Insert/Update data into BigQuery
    print("Uploading to BigQuery...")
    merge_via_staging(DATASET_ID, TABLE_ID, STAGING_TABLE_ID, json_record)


if __name__ == "__main__":
    main()
