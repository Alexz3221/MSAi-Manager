from __future__ import annotations

import argparse
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from pathlib import Path

from msai_core.matching import build_matches, load_customer_profiles


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
    effective_date: str | None
    requires_customer_action: bool
    summary: str
    actions: list[str]
    customer_raw_path: Path
    raw_msa_path: Path
    matching_services: list[str]


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


def build_notifications() -> list[Notification]:
    customer_profiles = load_customer_profiles()
    notifications: list[Notification] = []

    for profile in customer_profiles.values():
        for match in build_matches(profile.company_id):
            notifications.append(
                Notification(
                    account=profile.company_name,
                    contacts=profile.contacts,
                    msa_id=match.msa_id,
                    subject=match.subject,
                    date=match.date,
                    effective_date=match.effective_date,
                    requires_customer_action=match.requires_customer_action,
                    summary=match.summary,
                    actions=match.actions,
                    customer_raw_path=profile.raw_customer_path,
                    raw_msa_path=match.raw_msa_path,
                    matching_services=match.matching_services,
                )
            )

    return notifications


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or "email"


def email_subject(notification: Notification) -> str:
    return f"Relevant Google Cloud MSA update: {notification.subject}"


def recipient_list(override_recipients: list[str] | None = None) -> list[str]:
    return override_recipients or DEFAULT_TEST_RECIPIENTS


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
      <p><strong>Effective date:</strong> {escape(notification.effective_date or "Not listed")}</p>
      <p><strong>Customer action required:</strong> {"Yes" if notification.requires_customer_action else "No"}</p>
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
        smtp.send_message(message)


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
    print(f"  matched_services: {services}")
    print(f"  text_preview: {preview.text_path}")
    print(f"  html_preview: {preview.html_path}")
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
    args = parser.parse_args()
    recipients = args.recipient or DEFAULT_TEST_RECIPIENTS

    notifications = build_notifications()

    if not notifications:
        print("No customer service keywords matched cleaned MSA keywords.")
        return

    for notification in notifications:
        preview = write_email_preview(
            notification=notification,
            output_dir=Path(args.output_dir),
            sender=args.sender,
            recipients=recipients,
        )
        pretend_send_notification(notification, preview, recipients=recipients)

        if args.send:
            send_email(
                build_email_message(
                    notification=notification,
                    sender=args.sender,
                    recipients=recipients,
                )
            )


if __name__ == "__main__":
    main()
