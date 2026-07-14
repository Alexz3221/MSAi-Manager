from __future__ import annotations
import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from google.cloud import asset_v1 # tbd on this, but API is active now

ROOT = Path(__file__).parent
CUSTOMER_RAW_DIR = ROOT / "customer_data" / "raw"
CUSTOMER_KEYWORDS_DIR = ROOT / "customer_data" / "customer_keywords_cleaned"

# Will eventually connect the Asset Inventory API we want to use
FAKE_CLIENT_ASSETS: dict[str, list[str]] = {
    "acme_corp": ["BigQuery", "Cloud Storage", "Google Kubernetes Engine"],
    "globex": ["Cloud Storage", "Cloud Functions"],
    "initech": ["BigQuery", "Vertex AI", "Pub/Sub"],
}

@dataclass(frozen=True)
class ClientProfile:
    account: str
    client_id: str
    active_services: list[str]

def query_services(client_id: str) -> list[str]:
    """Return the active GCP services from the client project using the Asset API."""
    try:
        client = asset_v1.AssetServiceClient()
        project_resource = f"projects/{client_id}"
        
        response = client.list_assets(
            request={
                "parent": project_resource,
                "read_time": None,
                "content_type": asset_v1.ContentType.RESOURCE,
                # You can filter by specific asset_types or page_size if needed
            }
        )
        
        active_services = set()
        for asset in response:
            # Asset names typically look like: //compute.googleapis.com/projects/...
            # We can extract the service name from the asset_type
            if asset.asset_type:
                # E.g., extracts "storage.googleapis.com" from "storage.googleapis.com/Bucket"
                service = asset.asset_type.split("/")[0]
                active_services.add(service)
        return list(active_services)

    except Exception as e:
        print(f"Error querying Asset API: {e}")
        # Fallback to mock data for safety during testing
        try:
            return FAKE_CLIENT_ASSETS[client_id]
        except KeyError:
            raise ValueError(f"No mock asset data for client_id '{client_id}'.")

def build_profile(account_name: str, client_id: str) -> ClientProfile:
    # Changed from fetch_active_services to query_services
    services = query_services(client_id) 
    return ClientProfile(account=account_name, client_id=client_id, active_services=services)

def normalize_service_name(service: str) -> str:
    """Match the casefold-on-read convention used by combine_and_send.py / msa_chatbot.py."""
    return service.strip().casefold()
 
def build_profile(account_name: str, client_id: str) -> ClientProfile:
    services = wquery_services(client_id)
    return ClientProfile(account=account_name, client_id=client_id, active_services=services)
 
def write_keyword_csv(profile: ClientProfile) -> Path:
    """Write one service per row - matches read_keywords() in combine_and_send.py."""
    CUSTOMER_KEYWORDS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CUSTOMER_KEYWORDS_DIR / f"{profile.account}.csv"
 
    with out_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        for service in profile.active_services:
            writer.writerow([normalize_service_name(service)])
 
    return out_path
 
def write_raw_profile(profile: ClientProfile) -> Path:
    """Write a human-readable raw profile - matches raw_customer_path_for()."""
    CUSTOMER_RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CUSTOMER_RAW_DIR / f"{profile.account}.txt"
 
    lines = [
        f"Account: {profile.account}",
        f"Client ID: {profile.client_id}",
        "Active services:",
    ]
    lines += [f"  - {service}" for service in profile.active_services]
 
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
 
def generate_test_fixtures() -> None:
    """Generate CSV + raw files for every test client, for a quick pipeline test."""
    for client_id in FAKE_CLIENT_ASSETS:
        profile = build_profile(account_name=client_id, client_id=client_id)
        csv_path = write_keyword_csv(profile)
        raw_path = write_raw_profile(profile)
        print(f"{profile.account}: wrote {csv_path.name} and {raw_path.name}")
 
 
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull a client's active GCP services and write files that "
        "combine_and_send.py / msa_chatbot.py can match against."
    )
    parser.add_argument("--client-id", help="Client/project ID to look up.")
    parser.add_argument(
        "--account-name",
        help="Account name to use for output filenames (defaults to --client-id).",
    )
    parser.add_argument(
        "--generate-test-fixtures",
        action="store_true",
        help="Ignore --client-id and write CSV/raw files for every mock test client.",
    )
    args = parser.parse_args()
 
    if args.generate_test_fixtures:
        generate_test_fixtures()
        return
    if not args.client_id:
        parser.error("Provide --client-id or use --generate-test-fixtures.")
 
    account_name = args.account_name or args.client_id
    profile = build_profile(account_name=account_name, client_id=args.client_id)
    csv_path = write_keyword_csv(profile)
    raw_path = write_raw_profile(profile)
 
    print(f"Wrote {csv_path}")
    print(f"Wrote {raw_path}")
 
if __name__ == "__main__":
    main()
