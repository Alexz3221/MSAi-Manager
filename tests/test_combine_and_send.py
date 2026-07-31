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
    def test_email_rendering_includes_googley_severity_treatment(self) -> None:
        notice = notification(distribution_date="2026-07-20")

        html = combine_and_send.render_html_email(notice)
        text = combine_and_send.render_text_email(notice)

        self.assertIn("Cloud MSA Manager", html)
        self.assertIn("Action required", html)
        self.assertIn("#d93025", html)
        self.assertIn("class=\"grid\"", html)
        self.assertIn("Severity: Action required", text)

    def test_queue_loader_uses_canonical_client_columns(self) -> None:
        captured: dict[str, str] = {}

        def fake_query(query: str, parameters: object) -> list[dict[str, object]]:
            captured["query"] = query
            return []

        with (
            patch.dict(
                bigquery.os.environ,
                {
                    "BQ_PROJECT_ID": "demo-project",
                    "BQ_DATASET": "msa_manager",
                    "BQ_QUEUE_DATASET": "msa_dataset",
                },
                clear=False,
            ),
            patch.object(
                bigquery,
                "_queue_column_names",
                return_value=frozenset(
                    {"msa_id", "client_id", "update_details", "processed_at", "status"}
                ),
            ),
            patch.object(bigquery, "_query_records", side_effect=fake_query),
        ):
            bigquery.load_pending_queue_records(date(2026, 7, 20))

        self.assertIn("`demo-project.msa_dataset.msa_daily_queue`", captured["query"])
        self.assertIn("TRIM(q.`client_id`) AS client_id", captured["query"])
        self.assertIn("MAX(q.update_details) AS update_details", captured["query"])

    def test_queue_loader_supports_legacy_customer_columns(self) -> None:
        captured: dict[str, str] = {}

        def fake_query(query: str, parameters: object) -> list[dict[str, object]]:
            captured["query"] = query
            return []

        with (
            patch.dict(
                bigquery.os.environ,
                {
                    "BQ_PROJECT_ID": "demo-project",
                    "BQ_DATASET": "msa_manager",
                    "BQ_QUEUE_DATASET": "msa_manager",
                },
                clear=False,
            ),
            patch.object(
                bigquery,
                "_queue_column_names",
                return_value=frozenset(
                    {"msa_id", "customer_id", "details", "processed_at", "status"}
                ),
            ),
            patch.object(bigquery, "_query_records", side_effect=fake_query),
        ):
            bigquery.load_pending_queue_records(date(2026, 7, 20))

        self.assertIn("`demo-project.msa_manager.msa_daily_queue`", captured["query"])
        self.assertIn("TRIM(q.`customer_id`) AS client_id", captured["query"])
        self.assertIn("MAX(q.details) AS update_details", captured["query"])

    def test_queue_status_updates_use_canonical_client_column(self) -> None:
        captured: list[str] = []

        def fake_execute(query: str, parameters: object) -> int:
            captured.append(query)
            return 1

        with (
            patch.dict(
                bigquery.os.environ,
                {
                    "BQ_PROJECT_ID": "demo-project",
                    "BQ_QUEUE_DATASET": "msa_dataset",
                },
                clear=False,
            ),
            patch.object(
                bigquery,
                "_queue_column_names",
                return_value=frozenset(
                    {"msa_id", "client_id", "update_details", "processed_at", "status"}
                ),
            ),
            patch.object(bigquery, "_execute_dml", side_effect=fake_execute),
        ):
            bigquery.claim_queue_record("msa-demo", "example-project", date(2026, 7, 20))
            bigquery.mark_queue_record_sent(
                "msa-demo",
                "example-project",
                date(2026, 7, 20),
            )
            bigquery.mark_queue_record_failed(
                "msa-demo",
                "example-project",
                date(2026, 7, 20),
            )

        self.assertEqual(len(captured), 3)
        self.assertTrue(all("TRIM(q.`client_id`) = @client_id" in query for query in captured))

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

    def test_queue_customer_name_resolves_to_profile_contacts(self) -> None:
        profile = SimpleNamespace(
            company_id="pinehollow_retail_corp",
            company_name="Pinehollow Retail Corp.",
            contacts=["pinehollow@example.com"],
            raw_customer_path="",
            services={"bigquery": {"bigquery"}},
        )

        with patch.object(combine_and_send.matching, "read_text", return_value=""):
            result = combine_and_send.notification_from_queue_record(
                queue_record(client_id="Pinehollow Retail Corp."),
                {"pinehollow_retail_corp": profile},
            )

        self.assertEqual(result.account, "Pinehollow Retail Corp.")
        self.assertEqual(result.contacts, ["pinehollow@example.com"])
        self.assertEqual(result.queue_client_id, "Pinehollow Retail Corp.")

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
