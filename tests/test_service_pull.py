from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import service_pull


class FakeAssetClient:
    def __init__(self, *, policies=(), project_assets=None) -> None:
        self.policies = list(policies)
        self.project_assets = project_assets or {}
        self.search_requests: list[dict[str, object]] = []
        self.list_requests: list[dict[str, object]] = []

    def search_iam_policies(self, request):
        self.search_requests.append(request)
        return self.policies

    def list_assets(self, request):
        self.list_requests.append(request)
        project_id = str(request["parent"]).split("/", 1)[1]
        result = self.project_assets[project_id]
        if isinstance(result, Exception):
            raise result
        return result


class FakeBlob:
    def __init__(self, name: str, uploads: dict[str, tuple[str, str]]) -> None:
        self.name = name
        self.uploads = uploads

    def upload_from_string(self, content: str, *, content_type: str) -> None:
        self.uploads[self.name] = (content, content_type)


class FakeBucket:
    def __init__(self, uploads: dict[str, tuple[str, str]]) -> None:
        self.uploads = uploads

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self.uploads)


class FakeStorageClient:
    def __init__(self) -> None:
        self.bucket_name: str | None = None
        self.uploads: dict[str, tuple[str, str]] = {}

    def bucket(self, name: str) -> FakeBucket:
        self.bucket_name = name
        return FakeBucket(self.uploads)


class FailingStorageClient:
    def bucket(self, name: str):
        raise PermissionError(f"cannot write {name}")


def asset(name: str, asset_type: str):
    return SimpleNamespace(name=name, asset_type=asset_type)


class ServicePullTests(unittest.TestCase):
    def test_project_lookup_returns_sorted_raw_assets(self) -> None:
        client = FakeAssetClient(
            project_assets={
                "customer-project": [
                    asset(
                        "//storage.googleapis.com/customer-bucket",
                        "storage.googleapis.com/Bucket",
                    ),
                    asset(
                        "//bigquery.googleapis.com/projects/customer-project/"
                        "datasets/data/tables/events",
                        "bigquery.googleapis.com/Table",
                    ),
                ]
            }
        )

        assets = service_pull.query_assets("customer-project", client=client)

        self.assertEqual(
            assets,
            [
                service_pull.AssetRecord(
                    name="//bigquery.googleapis.com/projects/customer-project/"
                    "datasets/data/tables/events",
                    asset_type="bigquery.googleapis.com/Table",
                ),
                service_pull.AssetRecord(
                    name="//storage.googleapis.com/customer-bucket",
                    asset_type="storage.googleapis.com/Bucket",
                ),
            ],
        )
        self.assertEqual(
            client.list_requests[0]["parent"],
            "projects/customer-project",
        )

    def test_email_lookup_resolves_projects_with_explicit_scope(self) -> None:
        client = FakeAssetClient(
            policies=[
                SimpleNamespace(
                    resource="//cloudresourcemanager.googleapis.com/projects/project-a"
                ),
                SimpleNamespace(
                    resource="//compute.googleapis.com/projects/project-b/zones/us/test"
                ),
            ],
            project_assets={
                "project-a": [
                    asset(
                        "//run.googleapis.com/projects/project-a/locations/us/"
                        "services/api",
                        "run.googleapis.com/Service",
                    )
                ],
                "project-b": [
                    asset(
                        "//pubsub.googleapis.com/projects/project-b/topics/events",
                        "pubsub.googleapis.com/Topic",
                    )
                ],
            },
        )

        assets = service_pull.query_assets(
            "owner@example.com",
            scope="organizations/1234",
            client=client,
        )

        self.assertEqual(len(assets), 2)
        self.assertEqual(
            client.search_requests,
            [
                {
                    "scope": "organizations/1234",
                    "query": "policy:owner@example.com",
                }
            ],
        )

    def test_email_lookup_requires_scope(self) -> None:
        with patch.dict(
            os.environ,
            {"GCP_SCOPE": "", "GCP_ORGANIZATION": ""},
        ):
            with self.assertRaisesRegex(ValueError, "GCP_SCOPE"):
                service_pull.query_assets(
                    "owner@example.com",
                    client=FakeAssetClient(),
                )

    def test_api_errors_fail_instead_of_returning_mock_services(self) -> None:
        client = FakeAssetClient(
            project_assets={"customer-project": PermissionError("denied")}
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Cloud Asset Inventory query failed",
        ):
            service_pull.query_assets("customer-project", client=client)

    def test_export_maps_assets_to_cloud_profile_keywords(self) -> None:
        export = service_pull.AssetExport(
            account="sample_customer",
            client_id="customer-project",
            assets=[
                service_pull.AssetRecord(
                    name="//bigquery.googleapis.com/projects/customer-project/"
                    "datasets/data/tables/events",
                    asset_type="bigquery.googleapis.com/Table",
                )
            ],
        )

        self.assertEqual(
            service_pull.raw_export_text(export),
            "Account: sample_customer\n"
            "Client ID: customer-project\n"
            "Active services:\n"
            "- bigquery\n",
        )

    def test_common_api_domains_are_mapped_and_deduplicated(self) -> None:
        export = service_pull.AssetExport(
            account="sprinternship_bld_2026",
            client_id="sprinternship-bld-2026",
            assets=[
                service_pull.AssetRecord(
                    name=f"//{api_name}/projects/demo/resources/item",
                    asset_type=f"{api_name}/Resource",
                )
                for api_name in (
                    "bigquerydatatransfer.googleapis.com",
                    "cloudbuild.googleapis.com",
                    "cloudscheduler.googleapis.com",
                    "secretmanager.googleapis.com",
                    "cloudbuild.googleapis.com",
                )
            ],
        )

        output = service_pull.raw_export_text(export)

        self.assertEqual(
            output,
            "Account: sprinternship_bld_2026\n"
            "Client ID: sprinternship-bld-2026\n"
            "Active services:\n"
            "- bigquery data transfer\n"
            "- cloud build\n"
            "- cloud scheduler\n"
            "- secret manager\n",
        )
        self.assertNotIn("googleapis.com", output)

    def test_unmapped_api_uses_a_clean_domain_fallback(self) -> None:
        self.assertEqual(
            service_pull.keyword_for_asset(
                "//network-security.googleapis.com/projects/demo/resources/item",
                "network-security.googleapis.com/Resource",
            ),
            "network security",
        )

    def test_export_is_accepted_by_cloud_asset_checker(self) -> None:
        export = service_pull.AssetExport(
            account="sample_customer",
            client_id="customer-project",
            assets=[
                service_pull.AssetRecord(
                    name="//storage.googleapis.com/customer-bucket",
                    asset_type="storage.googleapis.com/Bucket",
                )
            ],
        )

        with (
            patch("google.cloud.storage.Client"),
            patch("google.cloud.bigquery.Client"),
        ):
            from scripts import asset_checker

        self.assertEqual(
            asset_checker.transform_txt_to_dict(
                service_pull.raw_export_text(export)
            ),
            {
                "account": "sample_customer",
                "client_id": "customer-project",
                "active_services": ["cloud storage"],
            },
        )

    def test_bucket_upload_writes_normalized_cloud_profile(self) -> None:
        export = service_pull.AssetExport(
            account="sample_customer",
            client_id="customer-project",
            assets=[
                service_pull.AssetRecord(
                    name="//storage.googleapis.com/customer-bucket",
                    asset_type="storage.googleapis.com/Bucket",
                )
            ],
        )
        client = FakeStorageClient()

        raw_uri = service_pull.upload_raw_export(
            export,
            "gs://dummy_client_bucket",
            client=client,
        )

        self.assertEqual(client.bucket_name, "dummy_client_bucket")
        self.assertEqual(
            raw_uri,
            "gs://dummy_client_bucket/raw_client_data/sample_customer.txt",
        )
        self.assertEqual(list(client.uploads), ["raw_client_data/sample_customer.txt"])
        self.assertEqual(
            client.uploads["raw_client_data/sample_customer.txt"][1],
            "text/plain; charset=utf-8",
        )
        self.assertEqual(
            client.uploads["raw_client_data/sample_customer.txt"][0],
            "Account: sample_customer\n"
            "Client ID: customer-project\n"
            "Active services:\n"
            "- cloud storage\n",
        )

    def test_bucket_upload_errors_fail_the_run(self) -> None:
        export = service_pull.AssetExport(
            account="sample_customer",
            client_id="customer-project",
            assets=[],
        )

        with self.assertRaisesRegex(RuntimeError, "Failed to upload raw assets"):
            service_pull.upload_raw_export(
                export,
                "dummy_client_bucket",
                client=FailingStorageClient(),
            )

    def test_account_name_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            service_pull.validate_account_name("../customer")


if __name__ == "__main__":
    unittest.main()
