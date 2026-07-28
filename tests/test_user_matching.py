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


def current_demo_profiles() -> dict[str, matching.CustomerProfile]:
    rows = {
        "amberline-financial-services": "Amberline Financial Services",
        "broadcom-corporation": "Broadcom Corporation",
        "cloud-ops-alpha": "Cloud Ops Alpha",
        "cobalt-ridge-manufacturing": "Cobalt Ridge Manufacturing",
        "concordance-inc": "Concordance Inc.",
        "endeavour-group-limited": "Endeavour Group Limited",
        "entisys-solutions-inc": "Entisys Solutions, Inc.",
        "fairfax-media-management-pty-limited": "Fairfax MEDIA Management Pty Limited",
        "finance-platform-prod": "Finance Platform Prod",
        "halden-&-marsh-consulting": "Halden & Marsh Consulting",
        "ingram-micro-inc": "Ingram Micro Inc.",
        "nationwide-news-pty-ltd": "Nationwide News Pty Ltd",
        "news-corporate-services-inc": "News Corporate Services Inc.",
        "northwind-traders-llc": "Northwind Traders LLC",
        "pinehollow-retail-corp": "Pinehollow Retail Corp.",
        "redshaw-logistics-group": "Redshaw Logistics Group",
        "retail-analytics-2026": "Retail Analytics 2026",
        "sada-systems-llc": "SADA Systems LLC",
        "sandbox-project-new": "Sandbox Project New",
        "smartcity-iot-dev": "SmartCity IoT Dev",
        "sprinternship-bld-2026": "sprinternship_bld_2026",
        "teg-pty-ltd": "TEG PTY LTD",
        "transurban-limited": "Transurban Limited",
        "trestle-and-co-media": "Trestle & Co. Media",
        "ttec-holdings-inc": "TTEC Holdings, Inc.",
        "vantage-point-analytics": "Vantage Point Analytics",
        "woolworths-group-limited": "Woolworths Group Limited",
    }
    return {
        company_id: customer_profile(company_id, company_name)
        for company_id, company_name in rows.items()
    }


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

    def test_fuzzy_domain_label_matches_broadcam_to_broadcom(self) -> None:
        profiles = {
            "broadcom-corporation": customer_profile(
                "broadcom-corporation",
                "Broadcom Corporation",
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
                users.resolve_role_company("aefnoo@broadcam.com"),
                ("customer", "broadcom-corporation"),
            )

    def test_current_demo_company_aliases_resolve(self) -> None:
        aliases = {
            "afs": "amberline-financial-services",
            "amberline": "amberline-financial-services",
            "brcm": "broadcom-corporation",
            "broadcam": "broadcom-corporation",
            "cloudops": "cloud-ops-alpha",
            "coa": "cloud-ops-alpha",
            "cobalt": "cobalt-ridge-manufacturing",
            "concordance": "concordance-inc",
            "endeavour": "endeavour-group-limited",
            "entisys": "entisys-solutions-inc",
            "fairfaxmedia": "fairfax-media-management-pty-limited",
            "fmm": "fairfax-media-management-pty-limited",
            "financeplatform": "finance-platform-prod",
            "haldenmarsh": "halden-&-marsh-consulting",
            "hmc": "halden-&-marsh-consulting",
            "ingrammicro": "ingram-micro-inc",
            "nationwide": "nationwide-news-pty-ltd",
            "newscorp": "news-corporate-services-inc",
            "northwind": "northwind-traders-llc",
            "pinehollow": "pinehollow-retail-corp",
            "redshaw": "redshaw-logistics-group",
            "retailanalytics": "retail-analytics-2026",
            "sada": "sada-systems-llc",
            "sandbox": "sandbox-project-new",
            "smartcity": "smartcity-iot-dev",
            "sprinternship": "sprinternship-bld-2026",
            "teg": "teg-pty-ltd",
            "transurban": "transurban-limited",
            "trestle": "trestle-and-co-media",
            "ttec": "ttec-holdings-inc",
            "vpa": "vantage-point-analytics",
            "woolies": "woolworths-group-limited",
            "woolworths": "woolworths-group-limited",
        }

        with (
            patch.dict(users.CUSTOMER_DOMAIN_ALIASES, {}, clear=True),
            patch(
                "msai_core.matching.load_customer_profiles",
                return_value=current_demo_profiles(),
            ),
        ):
            for alias, company_id in aliases.items():
                with self.subTest(alias=alias):
                    self.assertEqual(
                        users.resolve_role_company(f"demo@{alias}.com"),
                        ("customer", company_id),
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
