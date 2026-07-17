from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import bigquery_data
import msa_chatbot
import seed_bigquery


class BigQueryCustomerQueryTests(unittest.TestCase):
    def test_flat_customer_rows_are_grouped_for_the_application(self) -> None:
        sentinel = [{"company_id": "demo"}]

        with (
            patch.dict(
                os.environ,
                {
                    "BQ_PROJECT_ID": "test-project",
                    "BQ_DATASET": "test_dataset",
                    "BQ_CUSTOMERS_TABLE": "customer_profiles",
                },
                clear=False,
            ),
            patch.object(bigquery_data, "_query_records", return_value=sentinel) as query,
        ):
            self.assertEqual(bigquery_data.load_customer_records(), sentinel)

        sql = query.call_args.args[0]
        self.assertIn("TRIM(project_name)", sql)
        self.assertIn("project_name AS company_id", sql)
        self.assertIn("STRUCT(service AS name", sql)
        self.assertIn("GROUP BY project_name", sql)
        self.assertNotIn("SELECT company_id, company_name", sql)

    def test_empty_customers_and_one_msa_produce_healthy_empty_feed(self) -> None:
        msa_record = {
            "msa_id": "msa_demo",
            "raw_msa_path": "msa_demo.txt",
            "sent_date": "2026-07-17",
            "subject": "BigQuery demo notice",
            "headline": "BigQuery demo notice",
            "effective_date": None,
            "requires_customer_action": False,
            "affected_services": [{"name": "bigquery", "aliases": []}],
        }

        with (
            patch.dict(os.environ, {"DATA_SOURCE": "bigquery"}, clear=False),
            patch.object(bigquery_data, "load_customer_records", return_value=[]),
            patch.object(bigquery_data, "load_msa_records", return_value=[msa_record]),
        ):
            self.assertEqual(app.companies_payload(), {"companies": []})
            self.assertEqual(app.services_payload(), {"services": ["bigquery"]})
            self.assertEqual(app.feed_payload({})["count"], 0)


class LocalCustomerDataTests(unittest.TestCase):
    def test_legacy_and_flat_customer_documents_load_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            (directory / "legacy.json").write_text(
                json.dumps(
                    {
                        "company_id": "legacy_customer",
                        "company_name": "Legacy Customer",
                        "contacts": ["legal@example.com"],
                        "raw_customer_path": "customer_data/raw/legacy_customer.txt",
                        "services": [{"name": "bigquery", "aliases": ["bq"]}],
                    }
                ),
                encoding="utf-8",
            )
            (directory / "assets.json").write_text(
                json.dumps(
                    [
                        {
                            "project": "asset-project",
                            "service": "cloud storage",
                            "raw_uri": "//storage.googleapis.com/buckets/demo",
                        },
                        {"project": "asset-project", "service": "bigquery"},
                        {"project": "asset-project", "service": "bigquery"},
                    ]
                ),
                encoding="utf-8",
            )

            with (
                patch.object(msa_chatbot, "CUSTOMER_PROFILES_DIR", directory),
                patch.dict(os.environ, {"DATA_SOURCE": "local"}, clear=False),
            ):
                profiles = msa_chatbot.load_customer_profiles()

        self.assertEqual(set(profiles), {"legacy_customer", "asset_project"})
        self.assertEqual(set(profiles["asset_project"].services), {"bigquery", "cloud storage"})
        self.assertEqual(profiles["asset_project"].contacts, [])


class BigQuerySeedNormalizationTests(unittest.TestCase):
    def test_customer_documents_are_flattened_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            (directory / "legacy.json").write_text(
                json.dumps(
                    {
                        "company_id": "legacy",
                        "raw_customer_path": "customer_data/raw/legacy.txt",
                        "services": [
                            {
                                "name": "bigquery",
                                "source": "customer_data/raw/legacy.txt",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (directory / "summary.json").write_text(
                json.dumps([{"project": "demo", "service": "cloud storage"}]),
                encoding="utf-8",
            )
            (directory / "assets.json").write_text(
                json.dumps(
                    [
                        {
                            "project": "demo",
                            "service": "cloud storage",
                            "raw_uri": "//storage.googleapis.com/buckets/demo",
                        },
                        {
                            "project_name": "demo",
                            "service": "cloud storage",
                            "raw_uri": "//storage.googleapis.com/buckets/demo",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            with patch.object(seed_bigquery, "CUSTOMER_PROFILES_DIR", directory):
                rows = seed_bigquery.normalized_customer_records()

        self.assertEqual(
            rows,
            [
                {
                    "project_name": "demo",
                    "service": "cloud storage",
                    "raw_uri": "//storage.googleapis.com/buckets/demo",
                },
                {
                    "project_name": "legacy",
                    "service": "bigquery",
                    "raw_uri": "customer_data/raw/legacy.txt",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
