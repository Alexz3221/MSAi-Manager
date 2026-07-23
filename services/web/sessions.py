"""In-memory server-side sessions with a signed, HttpOnly cookie.

The cookie holds only a random token; all identity lives server-side in SESSIONS.
The token is HMAC-signed so a tampered cookie is rejected. In-memory means
sessions reset on restart and don't share across instances -- fine for a single
Cloud Run instance / demo; use a shared store (e.g. a table) if you scale out.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from http.cookies import SimpleCookie

COOKIE_NAME = "msai_session"
_TTL_SECONDS = int(os.environ.get("SESSION_TTL", str(8 * 3600)))
# MUST be set from env/secret in production. A fixed default here is only so dev
# works out of the box; a known secret means forgeable cookies.
_SECRET = os.environ.get("SESSION_SECRET", "dev-insecure-change-me").encode("utf-8")
_SECURE = os.environ.get("ENVIRONMENT", "prod").lower() not in {"dev", "local", "test"}

# token -> {"email","role","company_id","expires"}
SESSIONS: dict[str, dict] = {}


def _sign(token: str) -> str:
    mac = hmac.new(_SECRET, token.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{token}.{mac}"


def _unsign(value: str) -> str | None:
    if not value or "." not in value:
        return None
    token, _, mac = value.rpartition(".")
    expected = hmac.new(_SECRET, token.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return token if hmac.compare_digest(mac, expected) else None


def create_session(email: str, role: str, company_id: str | None) -> str:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"email": email, "role": role, "company_id": company_id,
                       "expires": time.time() + _TTL_SECONDS}
    return _sign(token)


def session_cookie_header(signed: str) -> tuple[str, str]:
    c = SimpleCookie()
    c[COOKIE_NAME] = signed
    m = c[COOKIE_NAME]
    m["httponly"] = True
    m["path"] = "/"
    m["samesite"] = "Strict"
    m["max-age"] = _TTL_SECONDS
    if _SECURE:
        m["secure"] = True
    return ("Set-Cookie", m.OutputString())


def clear_cookie_header() -> tuple[str, str]:
    c = SimpleCookie()
    c[COOKIE_NAME] = ""
    m = c[COOKIE_NAME]
    m["path"] = "/"
    m["max-age"] = 0
    return ("Set-Cookie", m.OutputString())


def session_from_cookie(cookie_header: str | None) -> dict | None:
    """Given the raw Cookie: header value, return the live session or None."""
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:  # noqa: BLE001
        return None
    if COOKIE_NAME not in jar:
        return None
    token = _unsign(jar[COOKIE_NAME].value)
    if token is None:
        return None
    sess = SESSIONS.get(token)
    if sess is None:
        return None
    if sess["expires"] < time.time():
        SESSIONS.pop(token, None)
        return None
    return sess


def destroy(cookie_header: str | None) -> None:
    if not cookie_header:
        return
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:  # noqa: BLE001
        return
    if COOKIE_NAME in jar:
        token = _unsign(jar[COOKIE_NAME].value)
        if token:
            SESSIONS.pop(token, None)
