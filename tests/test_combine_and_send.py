from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import combine_and_send


def notification(
    *,
    msa_id: str = "msa-demo",
    distribution_date: str | None,
) -> combine_and_send.Notification:
    return combine_and_send.Notification(
        account="Example Customer",
        contacts=["customer@example.com"],
        msa_id=msa_id,
        subject="Example MSA",
        date="2026-07-01",
        distribution_date=distribution_date,
        effective_date="2026-09-01",
        requires_customer_action=True,
        summary="Example summary",
        actions=["Review the change."],
        customer_raw_path=Path("customer.txt"),
        raw_msa_path=Path("msa.txt"),
        matching_services=["bigquery"],
    )


class CombineAndSendSchedulingTests(unittest.TestCase):
    def test_build_notifications_preserves_distribution_date(self) -> None:
        profile = SimpleNamespace(
            company_id="example_customer",
            company_name="Example Customer",
            contacts=[],
            raw_customer_path=Path("customer.txt"),
        )
        match = SimpleNamespace(
            msa_id="msa-demo",
            subject="Example MSA",
            date="2026-07-01",
            distribution_date="2026-07-25",
            effective_date=None,
            requires_customer_action=False,
            summary="Summary",
            actions=[],
            raw_msa_path=Path("msa.txt"),
            matching_services=["bigquery"],
        )

        with (
            patch.object(
                combine_and_send,
                "load_customer_profiles",
                return_value={"example_customer": profile},
            ),
            patch.object(
                combine_and_send,
                "build_matches",
                return_value=[match],
            ),
        ):
            notifications = combine_and_send.build_notifications()

        self.assertEqual(notifications[0].distribution_date, "2026-07-25")

    def test_missing_past_and_current_dates_are_due(self) -> None:
        as_of = date(2026, 7, 20)

        self.assertTrue(
            combine_and_send.notification_is_due(
                notification(distribution_date=None),
                as_of,
            )
        )
        self.assertTrue(
            combine_and_send.notification_is_due(
                notification(distribution_date="2026-07-19"),
                as_of,
            )
        )
        self.assertTrue(
            combine_and_send.notification_is_due(
                notification(distribution_date="2026-07-20"),
                as_of,
            )
        )

    def test_future_date_is_not_due(self) -> None:
        self.assertFalse(
            combine_and_send.notification_is_due(
                notification(distribution_date="2026-07-21"),
                date(2026, 7, 20),
            )
        )

    def test_invalid_distribution_date_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid ISO date"):
            combine_and_send.notification_is_due(
                notification(distribution_date="July 21"),
                date(2026, 7, 20),
            )

    def test_send_mode_only_sends_notifications_due_as_of_run_date(self) -> None:
        due = notification(msa_id="msa-due", distribution_date="2026-07-19")
        future = notification(
            msa_id="msa-future",
            distribution_date="2026-07-21",
        )
        preview = combine_and_send.EmailPreview(
            text_path=Path("preview.txt"),
            html_path=Path("preview.html"),
            eml_path=Path("preview.eml"),
        )

        with (
            patch.object(combine_and_send, "load_dotenv"),
            patch.object(
                combine_and_send,
                "build_notifications",
                return_value=[due, future],
            ),
            patch.object(
                combine_and_send,
                "write_email_preview",
                return_value=preview,
            ),
            patch.object(combine_and_send, "pretend_send_notification") as pretend,
            patch.object(combine_and_send, "print_scheduled_notification") as scheduled,
            patch.object(combine_and_send, "send_email") as send,
            patch.object(
                sys,
                "argv",
                ["combine_and_send", "--send", "--as-of", "2026-07-20"],
            ),
        ):
            with redirect_stdout(StringIO()):
                combine_and_send.main()

        self.assertEqual(pretend.call_count, 1)
        self.assertIs(pretend.call_args.args[0], due)
        scheduled.assert_called_once_with(future, preview)
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
