from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.cloud import asset_v1, storage

ROOT = Path(__file__).resolve().parents[1]
CUSTOMER_RAW_DIR = ROOT / "customer_data" / "raw"
DEFAULT_RAW_PREFIX = "raw_client_data"
ACCOUNT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class AssetRecord:
    name: str
    asset_type: str


@dataclass(frozen=True)
class AssetExport:
    account: str
    client_id: str
    assets: list[AssetRecord]


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


def assets_for_project(client: Any, project_id: str) -> set[AssetRecord]:
    response = client.list_assets(
        request={
            "parent": f"projects/{project_id}",
            "content_type": asset_v1.ContentType.RESOURCE,
        }
    )
    return {
        AssetRecord(name=asset.name, asset_type=asset.asset_type)
        for asset in response
        if asset.name and asset.asset_type
    }


def query_assets(
    client_id: str,
    *,
    scope: str | None = None,
    client: Any | None = None,
) -> list[AssetRecord]:
    """Return unprocessed asset names and types from Cloud Asset Inventory.

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

        assets: set[AssetRecord] = set()
        for project_id in sorted(project_ids):
            assets.update(assets_for_project(asset_client, project_id))
        return sorted(assets, key=lambda asset: (asset.name, asset.asset_type))
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Cloud Asset Inventory query failed for {client_id!r}: {exc}"
        ) from exc


def build_export(
    account_name: str,
    client_id: str,
    *,
    scope: str | None = None,
    client: Any | None = None,
) -> AssetExport:
    return AssetExport(
        account=validate_account_name(account_name),
        client_id=client_id,
        assets=query_assets(client_id, scope=scope, client=client),
    )


def raw_export_text(export: AssetExport) -> str:
    """Serialize assets in the raw ``RESOURCE_NAME ASSET_TYPE`` format."""
    if not export.assets:
        return ""
    return "\n".join(
        f"{asset.name} {asset.asset_type}" for asset in export.assets
    ) + "\n"


def write_raw_export(
    export: AssetExport,
    directory: Path = CUSTOMER_RAW_DIR,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / f"{export.account}.txt"
    output_path.write_text(raw_export_text(export), encoding="utf-8")
    return output_path


def normalize_bucket_name(value: str) -> str:
    bucket_name = value.removeprefix("gs://").strip("/")
    if not bucket_name or "/" in bucket_name:
        raise ValueError("Bucket must be a bucket name or gs:// bucket URI.")
    return bucket_name


def object_name(prefix: str, filename: str) -> str:
    clean_prefix = prefix.strip("/")
    return f"{clean_prefix}/{filename}" if clean_prefix else filename


def upload_raw_export(
    export: AssetExport,
    bucket_name: str,
    *,
    raw_prefix: str = DEFAULT_RAW_PREFIX,
    client: Any | None = None,
) -> str:
    """Upload the unprocessed asset export and return its gs:// URI."""
    normalized_bucket = normalize_bucket_name(bucket_name)
    raw_object = object_name(raw_prefix, f"{export.account}.txt")
    raw_uri = f"gs://{normalized_bucket}/{raw_object}"

    try:
        storage_client = client or storage.Client()
        bucket = storage_client.bucket(normalized_bucket)
        bucket.blob(raw_object).upload_from_string(
            raw_export_text(export),
            content_type="text/plain; charset=utf-8",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to upload raw assets to gs://{normalized_bucket}: {exc}"
        ) from exc

    return raw_uri


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull raw asset names and types from Cloud Asset Inventory "
        "and write them locally, to GCS, or both."
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
    export = build_export(account_name, args.client_id, scope=args.scope)

    if not args.no_local_output:
        raw_path = write_raw_export(export)
        print(f"Wrote {raw_path}")

    if args.bucket:
        raw_uri = upload_raw_export(
            export,
            args.bucket,
            raw_prefix=args.raw_prefix,
        )
        print(f"Uploaded {raw_uri}")


if __name__ == "__main__":
    main()
