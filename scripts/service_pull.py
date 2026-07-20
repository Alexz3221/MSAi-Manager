from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.cloud import asset_v1, storage

ROOT = Path(__file__).resolve().parents[1]
CUSTOMER_RAW_DIR = ROOT / "customer_data" / "raw"
CUSTOMER_KEYWORDS_DIR = ROOT / "customer_data" / "customer_keywords_cleaned"
DEFAULT_RAW_PREFIX = "raw_client_data"
DEFAULT_PROCESSED_PREFIX = "processed_client_data"
ACCOUNT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")

load_dotenv(ROOT / ".env")


# Maps Cloud Asset Inventory asset-type services to feed service keywords.
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


@dataclass(frozen=True)
class ClientProfile:
    account: str
    client_id: str
    active_services: list[str]


def validate_account_name(account_name: str) -> str:
    """Return a path-safe account identifier for filenames and object names."""
    if (
        not ACCOUNT_NAME_PATTERN.fullmatch(account_name)
        or account_name in {".", ".."}
    ):
        raise ValueError(
            "Account name must be 1-128 characters and contain only letters, "
            "numbers, dots, underscores, hyphens, or @."
        )
    return account_name


def normalize_service_name(service: str) -> str:
    """Match the casefold-on-read convention used by downstream consumers."""
    return service.strip().casefold()


def service_for_asset_type(asset_type: str) -> str:
    service_api_name = asset_type.split("/", 1)[0]
    return API_TO_KEYWORD_MAP.get(service_api_name, service_api_name)


def project_id_from_resource(resource: str) -> str | None:
    match = re.search(r"(?:^|/)projects/([^/]+)", resource)
    return match.group(1) if match else None


def project_ids_for_principal(
    client: Any,
    principal: str,
    scope: str,
) -> set[str]:
    response = client.search_iam_policies(
        request={"scope": scope, "query": f"policy:{principal}"}
    )
    return {
        project_id
        for policy in response
        if (project_id := project_id_from_resource(policy.resource))
    }


def services_for_project(client: Any, project_id: str) -> set[str]:
    response = client.list_assets(
        request={
            "parent": f"projects/{project_id}",
            "content_type": asset_v1.ContentType.RESOURCE,
        }
    )
    return {
        service_for_asset_type(asset.asset_type)
        for asset in response
        if asset.asset_type
    }


def query_services(
    client_id: str,
    *,
    scope: str | None = None,
    client: Any | None = None,
) -> list[str]:
    """Return real active services from Cloud Asset Inventory.

    A project ID is queried directly. An email principal is first resolved to
    projects by searching IAM policies within GCP_SCOPE or GCP_ORGANIZATION.
    Authentication and API errors fail the run instead of generating mock data.
    """
    asset_client = client or asset_v1.AssetServiceClient()

    try:
        if "@" in client_id:
            resolved_scope = (
                scope
                or os.environ.get("GCP_SCOPE")
                or os.environ.get("GCP_ORGANIZATION")
            )
            if not resolved_scope:
                raise ValueError(
                    "GCP_SCOPE or GCP_ORGANIZATION must be set when --client-id "
                    "is an email principal. Use projects/PROJECT_ID, "
                    "folders/FOLDER_NUMBER, or organizations/ORG_NUMBER."
                )
            project_ids = project_ids_for_principal(
                asset_client,
                client_id,
                resolved_scope,
            )
            if not project_ids:
                raise RuntimeError(
                    f"No projects were found for principal {client_id!r} "
                    f"within {resolved_scope!r}."
                )
        else:
            project_ids = {client_id}

        active_services: set[str] = set()
        for project_id in sorted(project_ids):
            active_services.update(services_for_project(asset_client, project_id))
        return sorted(active_services)
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Cloud Asset Inventory query failed for {client_id!r}: {exc}"
        ) from exc


def build_profile(
    account_name: str,
    client_id: str,
    *,
    scope: str | None = None,
    client: Any | None = None,
) -> ClientProfile:
    return ClientProfile(
        account=validate_account_name(account_name),
        client_id=client_id,
        active_services=query_services(client_id, scope=scope, client=client),
    )


def raw_profile_text(profile: ClientProfile) -> str:
    lines = [
        f"Account: {profile.account}",
        f"Client ID: {profile.client_id}",
        "Active services:",
    ]
    lines.extend(f"  - {service}" for service in profile.active_services)
    return "\n".join(lines) + "\n"


def processed_profile_text(profile: ClientProfile, raw_reference: str) -> str:
    payload = {
        "company_id": profile.account,
        "company_name": profile.account.replace("_", " ").title(),
        "contacts": [],
        "raw_customer_path": raw_reference,
        "services": [
            {
                "name": normalize_service_name(service),
                "aliases": [],
                "confidence": 1.0,
                "source": raw_reference,
            }
            for service in profile.active_services
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def write_raw_profile(
    profile: ClientProfile,
    directory: Path = CUSTOMER_RAW_DIR,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / f"{profile.account}.txt"
    output_path.write_text(raw_profile_text(profile), encoding="utf-8")
    return output_path


def write_keyword_json(
    profile: ClientProfile,
    directory: Path = CUSTOMER_KEYWORDS_DIR,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / f"{profile.account}.json"
    raw_reference = f"customer_data/raw/{profile.account}.txt"
    output_path.write_text(
        processed_profile_text(profile, raw_reference),
        encoding="utf-8",
    )
    return output_path


def normalize_bucket_name(value: str) -> str:
    bucket_name = value.removeprefix("gs://").strip("/")
    if not bucket_name or "/" in bucket_name:
        raise ValueError("Bucket must be a bucket name or gs:// bucket URI.")
    return bucket_name


def object_name(prefix: str, filename: str) -> str:
    clean_prefix = prefix.strip("/")
    return f"{clean_prefix}/{filename}" if clean_prefix else filename


def upload_profile(
    profile: ClientProfile,
    bucket_name: str,
    *,
    raw_prefix: str = DEFAULT_RAW_PREFIX,
    processed_prefix: str = DEFAULT_PROCESSED_PREFIX,
    client: Any | None = None,
) -> tuple[str, str]:
    """Upload raw text and processed JSON and return their gs:// URIs."""
    normalized_bucket = normalize_bucket_name(bucket_name)
    raw_object = object_name(raw_prefix, f"{profile.account}.txt")
    processed_object = object_name(processed_prefix, f"{profile.account}.json")
    raw_uri = f"gs://{normalized_bucket}/{raw_object}"
    processed_uri = f"gs://{normalized_bucket}/{processed_object}"

    try:
        storage_client = client or storage.Client()
        bucket = storage_client.bucket(normalized_bucket)
        bucket.blob(raw_object).upload_from_string(
            raw_profile_text(profile),
            content_type="text/plain; charset=utf-8",
        )
        bucket.blob(processed_object).upload_from_string(
            processed_profile_text(profile, raw_uri),
            content_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to upload profile to gs://{normalized_bucket}: {exc}"
        ) from exc

    return raw_uri, processed_uri


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull active GCP services from Cloud Asset Inventory and "
        "write a raw and processed customer profile locally, to GCS, or both."
    )
    parser.add_argument(
        "--client-id",
        default=os.environ.get("CUSTOMER_CLIENT_ID"),
        help="Project ID or email principal (env: CUSTOMER_CLIENT_ID).",
    )
    parser.add_argument(
        "--account-name",
        default=os.environ.get("CUSTOMER_ACCOUNT_NAME"),
        help=(
            "Output identifier; defaults to --client-id "
            "(env: CUSTOMER_ACCOUNT_NAME)."
        ),
    )
    parser.add_argument(
        "--scope",
        default=os.environ.get("GCP_SCOPE") or os.environ.get("GCP_ORGANIZATION"),
        help="IAM search scope required for email principals (env: GCP_SCOPE).",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("CUSTOMER_DATA_BUCKET"),
        help="GCS bucket name (env: CUSTOMER_DATA_BUCKET).",
    )
    parser.add_argument(
        "--raw-prefix",
        default=os.environ.get("CUSTOMER_RAW_PREFIX", DEFAULT_RAW_PREFIX),
        help=f"Raw object prefix (default: {DEFAULT_RAW_PREFIX}).",
    )
    parser.add_argument(
        "--processed-prefix",
        default=os.environ.get(
            "CUSTOMER_PROCESSED_PREFIX",
            DEFAULT_PROCESSED_PREFIX,
        ),
        help=f"Processed object prefix (default: {DEFAULT_PROCESSED_PREFIX}).",
    )
    parser.add_argument(
        "--no-local-output",
        action="store_true",
        help=(
            "Do not write repository-local output files "
            "(recommended for Cloud Run Jobs)."
        ),
    )
    args = parser.parse_args()

    if not args.client_id:
        parser.error("Provide --client-id or set CUSTOMER_CLIENT_ID.")
    if args.no_local_output and not args.bucket:
        parser.error("--no-local-output requires --bucket or CUSTOMER_DATA_BUCKET.")

    account_name = args.account_name or args.client_id
    profile = build_profile(account_name, args.client_id, scope=args.scope)

    if not args.no_local_output:
        raw_path = write_raw_profile(profile)
        processed_path = write_keyword_json(profile)
        print(f"Wrote {raw_path}")
        print(f"Wrote {processed_path}")

    if args.bucket:
        raw_uri, processed_uri = upload_profile(
            profile,
            args.bucket,
            raw_prefix=args.raw_prefix,
            processed_prefix=args.processed_prefix,
        )
        print(f"Uploaded {raw_uri}")
        print(f"Uploaded {processed_uri}")


if __name__ == "__main__":
    main()
