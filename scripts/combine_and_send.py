from __future__ import annotations

import argparse
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Any

from msai_core import matching
from msai_core.matching import load_customer_profiles


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "email_previews"
DEFAULT_TEST_RECIPIENTS = ["azhou@gccsprinternships.com"]
DOTENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class Notification:
    account: str
    contacts: list[str]
    msa_id: str
    subject: str
    date: str
    distribution_date: str | None
    effective_date: str | None
    requires_customer_action: bool
    summary: str
    actions: list[str]
    customer_raw_path: str
    raw_msa_path: str
    matching_services: list[str]
    queue_client_id: str | None = None


@dataclass(frozen=True)
class EmailPreview:
    text_path: Path
    html_path: Path
    eml_path: Path


def load_dotenv(path: Path = DOTENV_PATH) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def notification_from_queue_record(
    record: dict[str, Any],
    customer_profiles: dict[str, matching.CustomerProfile],
) -> Notification:
    msa_id = str(record.get("msa_id") or "").strip()
    client_id = str(record.get("client_id") or "").strip()
    if not msa_id or not client_id:
        raise ValueError("Queue rows require non-empty msa_id and client_id values.")
    if not record.get("msa_exists"):
        raise ValueError(f"{msa_id!r} does not exist in msa_updates.")

    profile = customer_profiles.get(matching.normalize_name(client_id))
    if profile is None:
        raise ValueError(
            f"client_id {client_id!r} does not exist in customer_profiles."
        )

    raw_msa_path = matching.resolve_data_path(
        record.get("raw_msa_path"),
        f"{msa_id}.txt",
        bucket_name=os.environ.get("MSA_DATA_BUCKET"),
    )
    affected_services = dict(
        matching.service_terms(service)
        for service in record.get("affected_services") or []
    )
    msa_profile = matching.MsaProfile(
        msa_id=msa_id,
        affected_services=affected_services,
        raw_msa_path=raw_msa_path,
        subject=record.get("subject"),
        headline=record.get("headline"),
        date=record.get("sent_date"),
        distribution_date=record.get("distribution_date"),
        effective_date=record.get("effective_date"),
        requires_customer_action=bool(
            record.get("requires_customer_action", False)
        ),
    )
    matching_services = matching.matching_customer_services(profile, msa_profile)
    if not matching_services:
        raise ValueError(
            f"{msa_id!r} has no affected service in common with client_id "
            f"{client_id!r}."
        )

    raw_text = matching.read_text(raw_msa_path)
    summary = matching.profile_summary(msa_profile, raw_text)
    queue_details = str(record.get("update_details") or "").strip()
    if summary == "No summary available." and queue_details:
        summary = queue_details

    return Notification(
        account=profile.company_name,
        contacts=profile.contacts,
        msa_id=msa_id,
        subject=msa_profile.subject
        or matching.extract_prefixed_line(raw_text, "Subject:", "Unknown subject"),
        date=msa_profile.date
        or matching.extract_prefixed_line(raw_text, "Date:", "Unknown date"),
        distribution_date=msa_profile.distribution_date,
        effective_date=msa_profile.effective_date,
        requires_customer_action=msa_profile.requires_customer_action,
        summary=summary,
        actions=matching.action_items(
            matching.section_lines(raw_text, "WHAT YOU NEED TO DO")
        ),
        customer_raw_path=profile.raw_customer_path,
        raw_msa_path=raw_msa_path,
        matching_services=matching_services,
        queue_client_id=client_id,
    )


def build_queue_notifications(
    as_of: date,
    invalid_entries: list[str] | None = None,
) -> list[Notification]:
    from msai_core.bigquery import load_pending_queue_records

    customer_profiles = load_customer_profiles()
    queue_records = load_pending_queue_records(as_of)
    notifications: list[Notification] = []
    discovered_errors: list[str] = []

    for record in queue_records:
        try:
            notifications.append(
                notification_from_queue_record(record, customer_profiles)
            )
        except (KeyError, TypeError, ValueError) as exc:
            discovered_errors.append(str(exc))

    if discovered_errors and invalid_entries is None:
        details = "\n".join(f"- {message}" for message in discovered_errors)
        raise RuntimeError(f"Invalid MSA daily queue entries:\n{details}")
    if invalid_entries is not None:
        invalid_entries.extend(discovered_errors)

    return notifications


def build_notifications(
    as_of: date | None = None,
    invalid_queue_entries: list[str] | None = None,
) -> list[Notification]:
    matching.data_source()
    return build_queue_notifications(
        as_of or date.today(),
        invalid_entries=invalid_queue_entries,
    )


def claim_notification(notification: Notification, as_of: date) -> bool:
    if notification.queue_client_id is None:
        return False

    from msai_core.bigquery import claim_queue_record

    affected_rows = claim_queue_record(
        msa_id=notification.msa_id,
        client_id=notification.queue_client_id,
        as_of=as_of,
    )
    return affected_rows > 0


def mark_notification_sent(
    notification: Notification,
    as_of: date,
) -> None:
    if notification.queue_client_id is None:
        return

    from msai_core.bigquery import mark_queue_record_sent

    affected_rows = mark_queue_record_sent(
        msa_id=notification.msa_id,
        client_id=notification.queue_client_id,
        as_of=as_of,
    )
    if affected_rows == 0:
        raise RuntimeError(
            "Email was accepted by SMTP, but no claimed queue row could be "
            f"marked sent for ({notification.msa_id!r}, "
            f"{notification.queue_client_id!r})."
        )


def mark_notification_failed(
    notification: Notification,
    as_of: date,
) -> None:
    if notification.queue_client_id is None:
        return

    from msai_core.bigquery import mark_queue_record_failed

    mark_queue_record_failed(
        msa_id=notification.msa_id,
        client_id=notification.queue_client_id,
        as_of=as_of,
    )


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or "email"


def email_subject(notification: Notification) -> str:
    return f"Relevant Google Cloud MSA update: {notification.subject}"


def recipient_list(override_recipients: list[str] | None = None) -> list[str]:
    return override_recipients or DEFAULT_TEST_RECIPIENTS


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO date {value!r}; expected YYYY-MM-DD.") from exc


def notification_is_due(notification: Notification, as_of: date) -> bool:
    """Return whether a notification should be delivered by the given date."""
    if not notification.distribution_date:
        return True
    return parse_iso_date(notification.distribution_date) <= as_of


def render_text_email(notification: Notification) -> str:
    services = ", ".join(notification.matching_services)
    actions = "\n".join(f"- {action}" for action in notification.actions)
    if not actions:
        actions = "- Review the linked MSA notice and confirm whether action is needed."

    return f"""Hello {notification.account} team,

We found a Google Cloud MSA notice that appears relevant to services your company uses.

MSA notice:
{notification.subject}

Notice date: {notification.date}
Distribution date: {notification.distribution_date or "Not listed"}
Effective date: {notification.effective_date or "Not listed"}
Customer action required: {"Yes" if notification.requires_customer_action else "No"}
Matched services: {services}

Summary:
{notification.summary}

Recommended next steps:
{actions}

Source files:
- Customer profile: {notification.customer_raw_path}
- MSA notice: {notification.raw_msa_path}

This is a generated preview from MSAi Manager.
"""


def render_html_email(notification: Notification) -> str:
    distribution_date = escape(notification.distribution_date or "Not listed")
    effective_date = escape(notification.effective_date or "Not listed")
    action_required = "Yes" if notification.requires_customer_action else "No"
    services = "".join(
        f"<span class=\"pill\">{escape(service)}</span>"
        for service in notification.matching_services
    )
    actions = "".join(
        f"<li>{escape(action)}</li>"
        for action in notification.actions
    )
    if not actions:
        actions = "<li>Review the linked MSA notice and confirm whether action is needed.</li>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(email_subject(notification))}</title>
  <style>
    body {{
      margin: 0;
      background: #eef5f1;
      color: #17211f;
      font-family: Georgia, "Times New Roman", serif;
    }}
    .email {{
      max-width: 720px;
      margin: 0 auto;
      padding: 28px;
      background: #ffffff;
      border: 1px solid #d7e1dc;
    }}
    .meta {{
      color: #65716d;
      font: 700 12px/1.3 Verdana, sans-serif;
      text-transform: uppercase;
    }}
    h1 {{
      font-size: 25px;
      line-height: 1.2;
      margin: 10px 0 14px;
    }}
    p, li {{
      font-size: 15px;
      line-height: 1.55;
    }}
    .pill {{
      display: inline-block;
      margin: 4px 6px 4px 0;
      padding: 7px 10px;
      border-radius: 999px;
      background: #e5f2ed;
      border: 1px solid #c8e0d7;
      color: #074d41;
      font: 700 12px/1 Verdana, sans-serif;
    }}
    .box {{
      margin: 18px 0;
      padding: 14px;
      background: #f7faf8;
      border: 1px solid #d7e1dc;
      border-radius: 8px;
    }}
    .path {{
      color: #65716d;
      font: 12px/1.4 Consolas, monospace;
      overflow-wrap: anywhere;
    }}
  </style>
</head>
<body>
  <main class="email">
    <div class="meta">{escape(notification.date)} | {escape(notification.msa_id)}</div>
    <h1>{escape(notification.subject)}</h1>
    <p>Hello {escape(notification.account)} team,</p>
    <p>We found a Google Cloud MSA notice that appears relevant to services your company uses.</p>

    <div class="box">
      <p><strong>Distribution date:</strong> {distribution_date}</p>
      <p><strong>Effective date:</strong> {effective_date}</p>
      <p><strong>Customer action required:</strong> {action_required}</p>
      <p><strong>Matched services:</strong></p>
      <div>{services}</div>
    </div>

    <h2>Summary</h2>
    <p>{escape(notification.summary)}</p>

    <h2>Recommended next steps</h2>
    <ul>{actions}</ul>

    <h2>Source files</h2>
    <p class="path">Customer profile: {escape(str(notification.customer_raw_path))}</p>
    <p class="path">MSA notice: {escape(str(notification.raw_msa_path))}</p>
  </main>
</body>
</html>
"""


def build_email_message(
    notification: Notification,
    sender: str,
    recipients: list[str] | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipient_list(recipients))
    message["Subject"] = email_subject(notification)
    message["X-MSAi-Preview"] = "true"
    if notification.contacts:
        message["X-MSAi-Original-Recipients"] = ", ".join(notification.contacts)
    message.set_content(render_text_email(notification))
    message.add_alternative(render_html_email(notification), subtype="html")
    return message


def write_email_preview(
    notification: Notification,
    output_dir: Path,
    sender: str,
    recipients: list[str] | None = None,
) -> EmailPreview:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_slug(notification.account)}__{safe_slug(notification.msa_id)}"
    text_path = output_dir / f"{filename}.txt"
    html_path = output_dir / f"{filename}.html"
    eml_path = output_dir / f"{filename}.eml"

    text_path.write_text(render_text_email(notification), encoding="utf-8")
    html_path.write_text(render_html_email(notification), encoding="utf-8")
    eml_path.write_text(
        build_email_message(
            notification=notification,
            sender=sender,
            recipients=recipients,
        ).as_string(),
        encoding="utf-8",
    )

    return EmailPreview(text_path=text_path, html_path=html_path, eml_path=eml_path)


def send_email(message: EmailMessage) -> None:
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")

    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.starttls()
        if smtp_username and smtp_password:
            smtp.login(smtp_username, smtp_password)
        refused_recipients = smtp.send_message(message)
        if refused_recipients:
            raise smtplib.SMTPRecipientsRefused(refused_recipients)


def pretend_send_notification(
    notification: Notification,
    preview: EmailPreview,
    recipients: list[str] | None = None,
) -> None:
    services = ", ".join(notification.matching_services)
    routed_recipients = ", ".join(recipient_list(recipients))
    original_recipients = ", ".join(notification.contacts) or "none"

    print("PRETEND EMAIL")
    print(f"  to: {routed_recipients}")
    print(f"  original_customer_contacts: {original_recipients}")
    print(f"  subject: {email_subject(notification)}")
    print(f"  company: {notification.account}")
    print(f"  distribution_date: {notification.distribution_date or 'not listed'}")
    print(f"  matched_services: {services}")
    print(f"  text_preview: {preview.text_path}")
    print(f"  html_preview: {preview.html_path}")
    print(f"  eml_preview: {preview.eml_path}")
    print()


def print_scheduled_notification(
    notification: Notification,
    preview: EmailPreview,
) -> None:
    print("SCHEDULED EMAIL - NOT DUE YET")
    print(f"  company: {notification.account}")
    print(f"  subject: {email_subject(notification)}")
    print(f"  distribution_date: {notification.distribution_date}")
    print(f"  eml_preview: {preview.eml_path}")
    print()


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Build MSA notification email previews and optionally send them."
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for generated .txt, .html, and .eml email previews.",
    )
    parser.add_argument(
        "--sender",
        default=os.environ.get("EMAIL_SENDER", "msa-manager@example.com"),
        help="Email sender address used in generated .eml files.",
    )
    parser.add_argument(
        "--recipient",
        action="append",
        help=(
            "Recipient for generated/sent emails. Defaults to "
            "azhou@gccsprinternships.com. Repeat to add multiple recipients."
        ),
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send email through SMTP_HOST. Omit this to only write previews.",
    )
    parser.add_argument(
        "--consume-queue",
        action="store_true",
        help=(
            "Claim BigQuery queue rows and mark them sent after successful SMTP "
            "delivery. Requires --send and at least one explicit --recipient."
        ),
    )
    parser.add_argument(
        "--as-of",
        type=parse_iso_date,
        default=date.today(),
        help=(
            "Date used to determine which notifications are due, in YYYY-MM-DD "
            "format (default: today)."
        ),
    )
    args = parser.parse_args()
    source = matching.data_source()
    if args.consume_queue and not args.send:
        parser.error("--consume-queue requires --send.")
    if args.consume_queue and not args.recipient:
        parser.error(
            "--consume-queue requires at least one explicit --recipient; the "
            "default address is only for test routing."
        )
    recipients = args.recipient or DEFAULT_TEST_RECIPIENTS
    invalid_queue_entries: list[str] = []

    notifications = build_notifications(
        as_of=args.as_of,
        invalid_queue_entries=invalid_queue_entries,
    )

    if not notifications:
        if invalid_queue_entries:
            details = "\n".join(
                f"- {message}" for message in invalid_queue_entries
            )
            raise RuntimeError(f"Invalid MSA daily queue entries:\n{details}")
        if source == "bigquery":
            print("No pending MSA daily queue entries need processing.")
        else:
            print("No customer service keywords matched cleaned MSA keywords.")
        return

    due_count = 0
    deferred_count = 0
    claimed_elsewhere_count = 0
    delivery_errors: list[str] = []
    for notification in notifications:
        try:
            is_due = notification_is_due(notification, args.as_of)
        except ValueError as exc:
            delivery_errors.append(
                f"Could not schedule ({notification.msa_id}, "
                f"{notification.queue_client_id or notification.account}): {exc}"
            )
            continue

        try:
            preview = write_email_preview(
                notification=notification,
                output_dir=Path(args.output_dir),
                sender=args.sender,
                recipients=recipients,
            )
        except Exception as exc:
            delivery_errors.append(
                f"Could not build preview for ({notification.msa_id}, "
                f"{notification.queue_client_id or notification.account}): {exc}"
            )
            continue

        if not is_due:
            deferred_count += 1
            print_scheduled_notification(notification, preview)
            continue

        claimed = False
        if args.consume_queue:
            try:
                claimed = claim_notification(notification, args.as_of)
            except Exception as exc:
                delivery_errors.append(
                    f"Could not claim ({notification.msa_id}, "
                    f"{notification.queue_client_id}): {exc}"
                )
                continue
            if not claimed:
                claimed_elsewhere_count += 1
                continue

        due_count += 1
        try:
            pretend_send_notification(notification, preview, recipients=recipients)

            if args.send:
                send_email(
                    build_email_message(
                        notification=notification,
                        sender=args.sender,
                        recipients=recipients,
                    )
                )
                if claimed:
                    mark_notification_sent(
                        notification,
                        args.as_of,
                    )
        except Exception as exc:
            if claimed:
                try:
                    mark_notification_failed(
                        notification,
                        args.as_of,
                    )
                except Exception as status_exc:
                    delivery_errors.append(
                        f"Could not release ({notification.msa_id}, "
                        f"{notification.queue_client_id}) after failure: "
                        f"{status_exc}"
                    )
            delivery_errors.append(
                f"Could not deliver ({notification.msa_id}, "
                f"{notification.queue_client_id or notification.account}): {exc}"
            )

    print(
        f"Due now: {due_count}; deferred until a future distribution date: "
        f"{deferred_count}; claimed by another worker: {claimed_elsewhere_count}."
    )

    all_errors = [*invalid_queue_entries, *delivery_errors]
    if all_errors:
        details = "\n".join(f"- {message}" for message in all_errors)
        raise RuntimeError(f"MSA queue processing completed with errors:\n{details}")


if __name__ == "__main__":
    main()
