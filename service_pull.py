from __future__ import annotations
import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from google.cloud import asset_v1 # tbd on this, but API is active now

ROOT = Path(__file__).parent
CUSTOMER_RAW_DIR = ROOT / "customer_data" / "raw"
CUSTOMER_KEYWORDS_DIR = ROOT / "customer_data" / "customer_keywords_cleaned"

# deleted fake client hardcode
# Maps raw GCP Asset API service names to user-friendly keywords
API_TO_KEYWORD_MAP: dict[str, str] = {
    "storage.googleapis.com": "cloud storage",
    "bigquery.googleapis.com": "bigquery",
    "compute.googleapis.com": "compute engine",
    "cloudfunctions.googleapis.com": "cloud functions",
    "container.googleapis.com": "google kubernetes engine",
    "pubsub.googleapis.com": "pub/sub",
    "aiplatform.googleapis.com": "vertex ai",
    "apigee.googleapis.com": "apigee",
    "apigeeconnect.googleapis.com": "apigee mcp",
    "sqladmin.googleapis.com": "cloud sql",
    "logging.googleapis.com": "cloud logging",
    "artifactregistry.googleapis.com": "artifact registry",
    "run.googleapis.com": "cloud run",
    "composer.googleapis.com": "cloud composer",
    "redis.googleapis.com": "memorystore for redis",
    "dialogflow.googleapis.com": "dialogflow es",
    "bigtableadmin.googleapis.com": "cloud bigtable",
    "iap.googleapis.com": "identity aware proxy",
    "dataflow.googleapis.com": "dataflow",
    "firestore.googleapis.com": "firestore",
}

def parse_raw_profile(path: Path) -> list[str]:
    """Parse active services from an existing raw customer profile text file."""
    services = []
    if not path.exists():
        return services

    # All known friendly service keywords
    all_friendly_services = list(set(API_TO_KEYWORD_MAP.values()))
    extra_services = [
        "apigee mcp", "apigee", "bigquery", "cloud storage",
        "compute engine", "cloud functions", "google kubernetes engine",
        "pub/sub", "vertex ai", "model context protocol", "mcp"
    ]
    for es in extra_services:
        if es not in all_friendly_services:
            all_friendly_services.append(es)

    content = path.read_text(encoding="utf-8", errors="replace")

    # Match each list line with known services
    for line in content.splitlines():
        line_strip = line.strip()
        if not line_strip:
            continue
        # Check if line is bullet point
        if line_strip.startswith("-") or line_strip.startswith("*"):
            text = line_strip.lstrip("-* ").strip().lower()
            # Find the longest matching service keyword first to avoid greedy substring matches
            sorted_svcs = sorted(all_friendly_services, key=len, reverse=True)
            for svc in sorted_svcs:
                if svc in text:
                    services.append(svc)
                    break

    # General scanning if no list items matched
    if not services:
        content_lower = content.lower()
        sorted_svcs = sorted(all_friendly_services, key=len, reverse=True)
        for svc in sorted_svcs:
            if svc in content_lower:
                services.append(svc)
    if "mcp" in services and "model context protocol" not in services:
        services.append("model context protocol")
    if "model context protocol" in services and "mcp" not in services:
        services.append("mcp")
    return sorted(list(set(services)))

@dataclass(frozen=True)
class ClientProfile:
    account: str
    client_id: str
    active_services: list[str]

def query_services(client_id: str) -> list[str]:
    """Return the active GCP services from the client project using the Asset API."""
    import os
    import hashlib
    
    try:
        client = asset_v1.AssetServiceClient()
        active_services = set()

        if "@" in client_id:
            scope = os.environ.get("GCP_SCOPE") or os.environ.get("GCP_ORGANIZATION")
            if not scope:
                raise ValueError(
                    "GCP_SCOPE or GCP_ORGANIZATION environment variable must be set "
                    "to search IAM policies by email principal."
                )
            response = client.search_iam_policies(
                request={
                    "scope": scope,
                    "query": f"policy:{client_id}"
                }
            )
            project_ids = set()
            for policy in response:
                if "projects/" in policy.resource:
                    proj = policy.resource.split("projects/")[-1]
                    projects_ids.add(proj)
            if not projects_ids:
                print(f"No projects found associated with email '{client_id}'.")
                return []
            for proj_id in project_ids:
                try:
                     assets_response = client.list_assets(
                        request={
                            "parent": f"projects/{proj_id}",
                            "read_time": None,
                            "content_type": asset_v1.ContentType.RESOURCE,
                        }
                    )
                    for asset in assets_response:
                        if asset.asset_type:
                            service_api_name = asset.asset_type.split("/")[0]
                            service = API_TO_KEYWORD_MAP.get(service_api_name, service_api_name)
                            active_services.add(service)
                except Exception as proj_err:
                    print(f"Error listing assets for project '{proj_id}': {proj_err}")
        else:
            # client_id is a project ID
            project_resource = f"projects/{client_id}"
            response = client.list_assets(
                request={
                    "parent": project_resource,
                    "read_time": None,
                    "content_type": asset_v1.ContentType.RESOURCE,
                }
            )
            for asset in response:
                if asset.asset_type:
                    service_api_name = asset.asset_type.split("/")[0]
                    service = API_TO_KEYWORD_MAP.get(service_api_name, service_api_name)
                    active_services.add(service)

        return list(active_services)

    except Exception as e:
        print(f"Error querying Asset API (falling back to mock profile/generation): {e}")

        normalized_id = client_id.strip().casefold().replace("-", "_").replace(" ", "_")
        for search_name in (client_id, normalized_id):
            raw_path = CUSTOMER_RAW_DIR / f"{search_name}.txt"
            if raw_path.exists():
                print(f"Found existing raw profile at {raw_path}. Parsing active services...")
                services = parse_raw_profile(raw_path)
                if services:
                    return services

        hash_val = int(hashlib.md5(client_id.encode('utf-8')).hexdigest(), 16)
        services = list(set(API_TO_KEYWORD_MAP.values()))
        num_services = 2 + (hash_val % 3)
        active_services = []
        for i in range(num_services):
            idx = (hash_val + i) % len(services)
            active_services.add(services[idx])
        return sorted(list(set(active_services)))    

def build_profile(account_name: str, client_id: str) -> ClientProfile:
    services = query_services(client_id) 
    return ClientProfile(account=account_name, client_id=client_id, active_services=services)

def normalize_service_name(service: str) -> str:
    """Match the casefold-on-read convention used by combine_and_send.py / msa_chatbot.py."""
    return service.strip().casefold()
 
def write_keyword_json(profile: ClientProfile) -> Path:
    """Write a structured cleaned customer profile for feed matching."""
    CUSTOMER_KEYWORDS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CUSTOMER_KEYWORDS_DIR / f"{profile.account}.json"
    payload = {
        "company_id": profile.account,
        "company_name": profile.account.replace("_", " ").title(),
        "contacts": [f"legal-contact+{profile.account}@example.com"],
        "raw_customer_path": f"customer_data/raw/{profile.account}.txt",
        "services": [
            {
                "name": normalize_service_name(service),
                "aliases": [],
                "confidence": 1.0,
                "source": f"customer_data/raw/{profile.account}.txt",
            }
            for service in profile.active_services
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
 
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
    """Generate JSON + raw files for every test client, for a quick pipeline test."""
    for client_id in FAKE_CLIENT_ASSETS:
        profile = build_profile(account_name=client_id, client_id=client_id)
        json_path = write_keyword_json(profile)
        raw_path = write_raw_profile(profile)
        print(f"{profile.account}: wrote {json_path.name} and {raw_path.name}")
 
 
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
        help="Ignore --client-id and write JSON/raw files for every mock test client.",
    )
    args = parser.parse_args()
 
    if args.generate_test_fixtures:
        generate_test_fixtures()
        return
    if not args.client_id:
        parser.error("Provide --client-id or use --generate-test-fixtures.")
 
    account_name = args.account_name or args.client_id
    profile = build_profile(account_name=account_name, client_id=args.client_id)
    json_path = write_keyword_json(profile)
    raw_path = write_raw_profile(profile)
 
    print(f"Wrote {json_path}")
    print(f"Wrote {raw_path}")
 
if __name__ == "__main__":
    main()
