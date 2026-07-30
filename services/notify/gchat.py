from __future__ import annotations
import json
import urllib.request
from urllib.parse import urlparse, parse_qs


def is_valid_gchat_webhook(url: str) -> bool:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "chat.googleapis.com":
        return False
    if not parsed.path.startswith("/v1/spaces/") or not parsed.path.endswith("/messages"):
        return False
    query = parse_qs(parsed.query)
    return bool(query.get("key")) and bool(query.get("token"))


def send_gchat_message(webhook_url: str, text: str) -> None:
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=UTF-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Google Chat webhook returned {resp.status}")