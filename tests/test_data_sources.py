from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import msai_core
from msai_core import bigquery, matching
from services.john.john_agent import agent as john
from services.web import app


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
            patch.object(bigquery, "_query_records", return_value=sentinel) as query,
        ):
            self.assertEqual(bigquery.load_customer_records(), sentinel)

        sql = query.call_args.args[0]
        self.assertIn("TRIM(project) AS project_name", sql)
        self.assertNotIn("TRIM(project_name)", sql)
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
            patch.object(bigquery, "load_customer_records", return_value=[]),
            patch.object(bigquery, "load_msa_records", return_value=[msa_record]),
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
                patch.object(matching, "CUSTOMER_PROFILES_DIR", directory),
                patch.dict(os.environ, {"DATA_SOURCE": "local"}, clear=False),
            ):
                profiles = matching.load_customer_profiles()

        self.assertEqual(set(profiles), {"legacy_customer", "asset_project"})
        self.assertEqual(set(profiles["asset_project"].services), {"bigquery", "cloud storage"})
        self.assertEqual(profiles["asset_project"].contacts, [])


class PackageBoundaryTests(unittest.TestCase):
    def test_core_package_exposes_the_established_matching_api(self) -> None:
        for name in msai_core.__all__:
            with self.subTest(name=name):
                self.assertIs(getattr(msai_core, name), getattr(matching, name))

    def test_john_delegates_queries_to_its_tool_module(self) -> None:
        class ToolContext:
            state = {"principal_email": "demo@example.com"}

        expected = {"notices": [], "count": 0}
        with patch.object(john.query, "find_msas", return_value=expected) as find_msas:
            result = john.find_msas_affecting_my_projects(ToolContext())

        self.assertEqual(result, expected)
        find_msas.assert_called_once_with(
            "demo@example.com", lookback_days=90, product=None
        )

    def test_john_exports_an_adk_root_agent_for_cloud_run(self) -> None:
        self.assertEqual(john.root_agent.name, "msa_advisor")
        self.assertEqual(john.root_agent.model, john.MODEL)

if __name__ == "__main__":
    unittest.main()
