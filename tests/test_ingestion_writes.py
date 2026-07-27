from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from google.cloud import bigquery

from scripts import asset_checker, msa_keyword_extractor


class IngestionWriteTests(unittest.TestCase):
    def test_customer_writer_replaces_matching_account_or_client_id(self) -> None:
        client = MagicMock()
        client.project = "test-project"
        client.get_table.return_value.schema = [
            bigquery.SchemaField("account", "STRING"),
            bigquery.SchemaField("client_id", "STRING"),
            bigquery.SchemaField("active_services", "STRING", mode="REPEATED"),
        ]

        asset_checker.merge_via_staging(
            "test_dataset",
            "customer_profiles",
            "customer_profiles_staging",
            {
                "account": "Demo",
                "client_id": "demo-project",
                "active_services": ["bigquery"],
            },
            client=client,
        )

        query = client.query.call_args.args[0]
        self.assertIn("DECLARE staged_client_id STRING", query)
        self.assertIn("SET (staged_client_id, staged_account)", query)
        self.assertIn("BEGIN TRANSACTION", query)
        self.assertIn("LOWER(TRIM(client_id)) = staged_client_id", query)
        self.assertIn("LOWER(TRIM(account)) = staged_account", query)
        self.assertNotIn("WHERE EXISTS", query)
        self.assertIn("INSERT INTO `test-project.test_dataset.customer_profiles`", query)
        client.delete_table.assert_called_once()

    def test_customer_writer_rejects_incomplete_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "account and client_id"):
            asset_checker.merge_via_staging(
                "test_dataset",
                "customer_profiles",
                "customer_profiles_staging",
                {"account": "Demo", "client_id": None, "active_services": []},
                client=MagicMock(),
            )

    def test_msa_writer_uses_environment_target_and_replaces_by_msa_id(self) -> None:
        client = MagicMock()
        client.project = "test-project"
        client.get_table.return_value.schema = [
            bigquery.SchemaField("msa_id", "STRING"),
            bigquery.SchemaField("raw_msa_path", "STRING"),
            bigquery.SchemaField("affected_services", "RECORD", mode="REPEATED"),
        ]
        profile = {
            "msa_id": "msa-demo",
            "raw_msa_path": "gs://msa-bucket/msa-demo.txt",
            "affected_services": [{"name": "bigquery", "aliases": []}],
        }

        with patch.dict(
            os.environ,
            {
                "BQ_DATASET": "test_dataset",
                "BQ_MSA_UPDATES_TABLE": "custom_msa_updates",
            },
            clear=False,
        ):
            self.assertEqual(
                msa_keyword_extractor.write_profile(profile, client=client),
                [],
            )

        query = client.query.call_args.args[0]
        target = "test-project.test_dataset.custom_msa_updates"
        self.assertIn(f"DELETE FROM `{target}`", query)
        self.assertIn(f"INSERT INTO `{target}`", query)
        self.assertIn("target.msa_id", query)
        client.delete_table.assert_called_once()


if __name__ == "__main__":
    unittest.main()
