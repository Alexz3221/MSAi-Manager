from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from msai_core import matching
from services.web import users


def customer_profile(company_id: str, company_name: str) -> matching.CustomerProfile:
    return matching.CustomerProfile(
        company_id=company_id,
        company_name=company_name,
        contacts=[],
        services={},
        raw_customer_path="",
    )


class UserMatchingTests(unittest.TestCase):
    def test_compact_domain_label_matches_spaced_company_name(self) -> None:
        profiles = {
            "vantage_point_analytics": customer_profile(
                "vantage_point_analytics",
                "Vantage Point Analytics",
            )
        }

        with (
            patch.dict(users.CUSTOMER_DOMAIN_ALIASES, {}, clear=True),
            patch("msai_core.matching.load_customer_profiles", return_value=profiles),
        ):
            self.assertEqual(
                users.resolve_role_company("demo@vantagepointanalytics.com"),
                ("customer", "vantage_point_analytics"),
            )

    def test_domain_alias_can_point_to_company_name(self) -> None:
        profiles = {
            "vantage_point_analytics": customer_profile(
                "vantage_point_analytics",
                "Vantage Point Analytics",
            )
        }

        with (
            patch.dict(
                users.CUSTOMER_DOMAIN_ALIASES,
                {"vpa.example": "Vantage Point Analytics"},
                clear=True,
            ),
            patch("msai_core.matching.load_customer_profiles", return_value=profiles),
        ):
            self.assertEqual(
                users.resolve_role_company("demo@vpa.example"),
                ("customer", "vantage_point_analytics"),
            )

    def test_fuzzy_domain_label_matches_confident_typo(self) -> None:
        profiles = {
            "vantage_point_analytics": customer_profile(
                "vantage_point_analytics",
                "Vantage Point Analytics",
            ),
            "pinehollow_retail_corp": customer_profile(
                "pinehollow_retail_corp",
                "Pinehollow Retail Corp.",
            ),
        }

        with (
            patch.dict(users.CUSTOMER_DOMAIN_ALIASES, {}, clear=True),
            patch("msai_core.matching.load_customer_profiles", return_value=profiles),
        ):
            self.assertEqual(
                users.resolve_role_company("demo@vantagepontanalytics.com"),
                ("customer", "vantage_point_analytics"),
            )

    def test_fuzzy_domain_label_rejects_ambiguous_best_fit(self) -> None:
        profiles = {
            "vantage_point_analytics": customer_profile(
                "vantage_point_analytics",
                "Vantage Point Analytics",
            ),
            "vantage_point_advisors": customer_profile(
                "vantage_point_advisors",
                "Vantage Point Advisors",
            ),
        }

        with (
            patch.dict(users.CUSTOMER_DOMAIN_ALIASES, {}, clear=True),
            patch("msai_core.matching.load_customer_profiles", return_value=profiles),
        ):
            self.assertEqual(
                users.resolve_role_company("demo@vantagepoint.com"),
                ("customer", None),
            )

    def test_login_refreshes_previous_unmapped_company(self) -> None:
        profiles = {
            "vantage_point_analytics": customer_profile(
                "vantage_point_analytics",
                "Vantage Point Analytics",
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "users.db"
            with patch.object(users, "DB_PATH", db_path):
                users.init_db()
                with (
                    patch.dict(users.CUSTOMER_DOMAIN_ALIASES, {}, clear=True),
                    patch("msai_core.matching.load_customer_profiles", return_value={}),
                ):
                    created, err = users.create_user(
                        "demo@vantagepointanalytics.com",
                        "password123",
                    )

                self.assertIsNone(err)
                self.assertIsNotNone(created)
                self.assertIsNone(created.company_id)

                with (
                    patch.dict(users.CUSTOMER_DOMAIN_ALIASES, {}, clear=True),
                    patch(
                        "msai_core.matching.load_customer_profiles",
                        return_value=profiles,
                    ),
                ):
                    refreshed = users.verify_user(
                        "demo@vantagepointanalytics.com",
                        "password123",
                    )

                self.assertIsNotNone(refreshed)
                self.assertEqual(refreshed.company_id, "vantage_point_analytics")
                with users._conn() as con:
                    stored = con.execute(
                        "SELECT company_id FROM users WHERE email = ?",
                        ("demo@vantagepointanalytics.com",),
                    ).fetchone()
                self.assertEqual(stored["company_id"], "vantage_point_analytics")


if __name__ == "__main__":
    unittest.main()
