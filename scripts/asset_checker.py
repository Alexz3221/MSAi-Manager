import json
import re
from google.cloud import bigquery, storage

BUCKET_NAME = "dummy_client_bucket"
FILE_PATH = "raw_client_data/sprinternship_bld_2026.txt" 

DATASET_ID = "msa_manager"
TABLE_ID = "customer_profiles"

def read_gcs_file(bucket_name: str, file_path: str) -> str:
    """Downloads text content directly from GCS into memory."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_path)

    # Read as text string
    return blob.download_as_text()


def transform_txt_to_dict(text_content: str) -> dict:
    """Parses your specific key-value/list plain text into a structured dictionary."""
    account_match = re.search(r"Account:\s*(.+)", text_content)
    client_id_match = re.search(r"Client ID:\s*(.+)", text_content)

    # Extract all bullet points under 'Active services'
    services = re.findall(r"^\s*-\s*(.+)$", text_content, re.MULTILINE)

    return {
        "account": account_match.group(1).strip() if account_match else None,
        "client_id": client_id_match.group(1).strip()
        if client_id_match
        else None,
        "active_services": [s.strip() for s in services],
    }


def insert_into_bigquery(
    dataset_id: str, table_id: str, record: dict
) -> None:
    #Inserts the structured record directly into a BigQuery table.
    bq_client = bigquery.Client()
    table_ref = f"{bq_client.project}.{dataset_id}.{table_id}"

    # insert_rows_json takes a list of dictionaries
    errors = bq_client.insert_rows_json(table_ref, [record])

    if errors:
        print(f"Failed to insert row: {errors}")
    else:
        print(f"Successfully inserted record into {table_ref}!")


def main():
    # Read .txt file from GCS
    print(f"Reading file gs://{BUCKET_NAME}/{FILE_PATH}...")
    raw_text = read_gcs_file(BUCKET_NAME, FILE_PATH)

    # Convert text JSON
    json_record = transform_txt_to_dict(raw_text)
    print("Parsed JSON record:")
    print(json.dumps(json_record, indent=2))

    # Upload data into BigQuery
    print("Uploading to BigQuery...")
    insert_into_bigquery(DATASET_ID, TABLE_ID, json_record)


if __name__ == "__main__":
    main()