from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from msai_core import bigquery
from scripts import combine_and_send


def notification(
    *,
    msa_id: str = "msa-demo",
    distribution_date: str | None,
    queue_client_id: str | None = None,
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
        customer_raw_path="gs://customer-bucket/customer.txt",
        raw_msa_path="gs://msa-bucket/msa.txt",
        matching_services=["bigquery"],
        queue_client_id=queue_client_id,
    )


def queue_record(
    *,
    msa_id: str = "msa-demo",
    client_id: str = "example-project",
    msa_exists: bool = True,
    distribution_date: str | None = "2026-07-20",
) -> dict[str, object]:
    return {
        "msa_id": msa_id,
        "client_id": client_id,
        "update_details": "Queue-specific fallback summary",
        "msa_exists": msa_exists,
        "raw_msa_path": "missing-raw-msa.txt",
        "sent_date": "2026-07-19",
        "distribution_date": distribution_date,
        "subject": "Queued BigQuery notice",
        "headline": "Queued BigQuery notice",
        "effective_date": None,
        "requires_customer_action": False,
        "affected_services": [{"name": "bigquery", "aliases": []}],
    }


class CombineAndSendSchedulingTests(unittest.TestCase):
    def test_build_notifications_preserves_distribution_date(self) -> None:
        profile = SimpleNamespace(
            company_id="example_customer",
            company_name="Example Customer",
            contacts=[],
            raw_customer_path="",
            services={"bigquery": {"bigquery"}},
        )

        with patch.object(combine_and_send.matching, "read_text", return_value=""):
            result = combine_and_send.notification_from_queue_record(
                queue_record(
                    client_id="example_customer",
                    distribution_date="2026-07-25",
                ),
                {"example_customer": profile},
            )

        self.assertEqual(result.distribution_date, "2026-07-25")

    def test_bigquery_mode_builds_only_pending_queue_notifications(self) -> None:
        profile = SimpleNamespace(
            company_id="example_project",
            company_name="Example Project",
            contacts=[],
            raw_customer_path="",
            services={"bigquery": {"bigquery"}},
        )
        as_of = date(2026, 7, 20)

        with (
            patch.object(
                combine_and_send.matching,
                "data_source",
                return_value="bigquery",
            ),
            patch.object(
                combine_and_send,
                "load_customer_profiles",
                return_value={"example_project": profile},
            ),
            patch.object(
                bigquery,
                "load_pending_queue_records",
                return_value=[queue_record()],
            ) as load_queue,
        ):
            notifications = combine_and_send.build_notifications(as_of=as_of)

        load_queue.assert_called_once_with(as_of)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].msa_id, "msa-demo")
        self.assertEqual(notifications[0].queue_client_id, "example-project")
        self.assertEqual(notifications[0].matching_services, ["bigquery"])

    def test_invalid_queue_join_fails_before_any_email_is_sent(self) -> None:
        as_of = date(2026, 7, 20)

        with (
            patch.object(
                combine_and_send.matching,
                "data_source",
                return_value="bigquery",
            ),
            patch.object(combine_and_send, "load_customer_profiles", return_value={}),
            patch.object(
                bigquery,
                "load_pending_queue_records",
                return_value=[queue_record(msa_exists=False)],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "does not exist in msa_updates"):
                combine_and_send.build_notifications(as_of=as_of)

    def test_queue_error_collection_keeps_valid_deliveries(self) -> None:
        profile = SimpleNamespace(
            company_id="example_project",
            company_name="Example Project",
            contacts=[],
            raw_customer_path="",
            services={"bigquery": {"bigquery"}},
        )
        errors: list[str] = []

        with (
            patch.object(
                combine_and_send,
                "load_customer_profiles",
                return_value={"example_project": profile},
            ),
            patch.object(
                bigquery,
                "load_pending_queue_records",
                return_value=[
                    queue_record(),
                    queue_record(msa_id="missing-msa", msa_exists=False),
                ],
            ),
        ):
            notifications = combine_and_send.build_queue_notifications(
                date(2026, 7, 20),
                invalid_entries=errors,
            )

        self.assertEqual([item.msa_id for item in notifications], ["msa-demo"])
        self.assertEqual(len(errors), 1)
        self.assertIn("missing-msa", errors[0])

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
        due = notification(
            msa_id="msa-due",
            distribution_date="2026-07-19",
            queue_client_id="example-project",
        )
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
            patch.object(combine_and_send, "mark_notification_sent") as mark_sent,
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
        mark_sent.assert_not_called()

    def test_partial_recipient_refusal_is_a_send_failure(self) -> None:
        message = combine_and_send.EmailMessage()
        smtp = MagicMock()
        smtp.__enter__.return_value.send_message.return_value = {
            "refused@example.com": (550, b"mailbox unavailable")
        }

        with (
            patch.dict(
                combine_and_send.os.environ,
                {"SMTP_HOST": "smtp.example.com"},
                clear=False,
            ),
            patch.object(combine_and_send.smtplib, "SMTP", return_value=smtp),
        ):
            with self.assertRaises(combine_and_send.smtplib.SMTPRecipientsRefused):
                combine_and_send.send_email(message)

    def test_successful_send_marks_the_queue_entry_sent(self) -> None:
        queued = notification(
            msa_id="msa-queued",
            distribution_date="2026-07-20",
            queue_client_id="example-project",
        )
        preview = combine_and_send.EmailPreview(
            text_path=Path("preview.txt"),
            html_path=Path("preview.html"),
            eml_path=Path("preview.eml"),
        )
        with (
            patch.object(combine_and_send, "load_dotenv"),
            patch.object(
                combine_and_send.matching,
                "data_source",
                return_value="bigquery",
            ),
            patch.object(
                combine_and_send,
                "build_notifications",
                return_value=[queued],
            ),
            patch.object(
                combine_and_send,
                "write_email_preview",
                return_value=preview,
            ),
            patch.object(combine_and_send, "pretend_send_notification"),
            patch.object(combine_and_send, "send_email") as send,
            patch.object(
                combine_and_send,
                "claim_notification",
                return_value=True,
            ) as claim,
            patch.object(combine_and_send, "mark_notification_sent") as mark_sent,
            patch.object(
                sys,
                "argv",
                [
                    "combine_and_send",
                    "--send",
                    "--consume-queue",
                    "--recipient",
                    "customer@example.com",
                    "--as-of",
                    "2026-07-20",
                ],
            ),
        ):
            with redirect_stdout(StringIO()):
                combine_and_send.main()

        send.assert_called_once()
        claim.assert_called_once_with(queued, date(2026, 7, 20))
        mark_sent.assert_called_once_with(
            queued,
            date(2026, 7, 20),
        )

    def test_smtp_failure_marks_the_queue_entry_retryable(self) -> None:
        queued = notification(
            msa_id="msa-queued",
            distribution_date="2026-07-20",
            queue_client_id="example-project",
        )
        preview = combine_and_send.EmailPreview(
            text_path=Path("preview.txt"),
            html_path=Path("preview.html"),
            eml_path=Path("preview.eml"),
        )
        with (
            patch.object(combine_and_send, "load_dotenv"),
            patch.object(
                combine_and_send.matching,
                "data_source",
                return_value="bigquery",
            ),
            patch.object(
                combine_and_send,
                "build_notifications",
                return_value=[queued],
            ),
            patch.object(
                combine_and_send,
                "write_email_preview",
                return_value=preview,
            ),
            patch.object(combine_and_send, "pretend_send_notification"),
            patch.object(
                combine_and_send,
                "send_email",
                side_effect=RuntimeError("SMTP failed"),
            ),
            patch.object(
                combine_and_send,
                "claim_notification",
                return_value=True,
            ),
            patch.object(combine_and_send, "mark_notification_sent") as mark_sent,
            patch.object(combine_and_send, "mark_notification_failed") as mark_failed,
            patch.object(
                sys,
                "argv",
                [
                    "combine_and_send",
                    "--send",
                    "--consume-queue",
                    "--recipient",
                    "customer@example.com",
                    "--as-of",
                    "2026-07-20",
                ],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "SMTP failed"):
                with redirect_stdout(StringIO()):
                    combine_and_send.main()

        mark_sent.assert_not_called()
        mark_failed.assert_called_once_with(
            queued,
            date(2026, 7, 20),
        )


if __name__ == "__main__":
    unittest.main()
