import json
import re
from google.cloud import bigquery, storage

storage_client = storage.Client()
bq_client = bigquery.Client()

BUCKET_NAME = "dummy_client_bucket"
FILE_PATH = "raw_client_data/sprinternship_bld_2026.txt" 

DATASET_ID = "msa_manager"
TABLE_ID = "customer_profiles"
STAGING_TABLE_ID = "customer_profiles_staging"

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
    dataset_id: str, target_table: str, staging_table: str, record: dict
) -> None:
    """Loads record into staging via batch load, then MERGEs into the main target table."""
    bq_client = bigquery.Client()
    target_ref = f"{bq_client.project}.{dataset_id}.{target_table}"
    staging_ref = f"{bq_client.project}.{dataset_id}.{staging_table}"

    # Step 1: Overwrite the staging table using a batch load job.
    # Batch loads completely bypass BigQuery's streaming buffer.
    load_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
    )

    print(f"Loading incoming record into staging table ({staging_ref})...")
    load_job = bq_client.load_table_from_json(
        [record], staging_ref, job_config=load_config
    )
    load_job.result()  # Wait for batch load to complete

    # Step 2: Merge staging table data into the main table
    merge_query = f"""
    MERGE `{target_ref}` T
    USING `{staging_ref}` S
    ON T.account = S.account
    WHEN MATCHED THEN
      UPDATE SET 
        client_id = S.client_id, 
        active_services = S.active_services
    WHEN NOT MATCHED THEN
      INSERT (account, client_id, active_services)
      VALUES (S.account, S.client_id, S.active_services)
    """

    print(f"Merging staging data into main table ({target_ref})...")
    query_job = bq_client.query(merge_query)
    query_job.result()  # Wait for query completion

    print(
        f"Successfully merged account '{record['account']}' into {target_ref}!"
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