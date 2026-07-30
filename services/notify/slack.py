from __future__ import annotations
import json
import re
import urllib.request

SLACK_WEBHOOK_RE = re.compile(
    r"^https://hooks\.slack\.com/services/T[\w]+/B[\w]+/[\w]+$"
)


def is_valid_slack_webhook(url: str) -> bool:
    return bool(SLACK_WEBHOOK_RE.match(url.strip()))


def send_slack_message(webhook_url: str, text: str) -> None:
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack webhook returned {resp.status}")