"""User store: SQLite table, bcrypt password hashes, server-derived role/company.

Role and company are NEVER taken from the registration form -- they are derived
from the email domain server-side (see auth.resolve_role_company). A user cannot
choose to be 'internal'.

NOT DONE HERE (must add before real customer data):
  - Email ownership is not verified. Someone can register ceo@broadcom.com
    without owning it and get that company's scope. Add an email-confirmation
    step before this gates anything real.
"""
from __future__ import annotations

import os
import sqlite3
import datetime as dt
import tempfile
from dataclasses import dataclass
from pathlib import Path

import bcrypt

#DB_PATH = Path(os.environ.get("USERS_DB", Path(__file__).resolve().parent / "users.db"))

DB_PATH = Path(os.environ.get("USERS_DB", Path(tempfile.gettempdir()) / "users.db"))

# Internal domains -> internal role. Server-side allowlist; never client-supplied.
INTERNAL_DOMAINS = {
    d.strip().lower()
    for d in os.environ.get("INTERNAL_DOMAINS", "google.com").split(",")
    if d.strip()
}


@dataclass(frozen=True)
class User:
    email: str
    role: str
    company_id: str | None


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email          TEXT PRIMARY KEY,
                password_hash  TEXT NOT NULL,
                role           TEXT NOT NULL,
                company_id     TEXT,
                created_at     TEXT NOT NULL
            )
        """)


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def resolve_role_company(email: str) -> tuple[str, str | None]:
    """Decide role + company from the email domain. Server-side only.

    Customer company_id resolution is best-effort against customer_profiles;
    adjust once that table holds real domain-keyed customer data.
    """
    domain = _domain(email)
    if domain in INTERNAL_DOMAINS:
        return "internal", None
    # customer: map domain label -> a company in customer_profiles
    try:
        from msai_core import matching
        label = domain.split(".")[0]
        profiles = matching.load_customer_profiles()
        hit = matching.find_company(label, profiles)
        if hit is not None:
            return "customer", profiles[hit].company_id
    except Exception:  # noqa: BLE001
        pass
    return "customer", None   # authenticated but unmapped -> sees nothing


def create_user(email: str, password: str) -> tuple[User | None, str | None]:
    email = email.strip().lower()
    if "@" not in email:
        return None, "Enter a valid email address."
    if len(password) < 8:
        return None, "Password must be at least 8 characters."
    role, company_id = resolve_role_company(email)
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO users (email, password_hash, role, company_id, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (email, pw_hash, role, company_id, dt.datetime.utcnow().isoformat()),
            )
    except sqlite3.IntegrityError:
        return None, "An account with that email already exists."
    return User(email=email, role=role, company_id=company_id), None


def verify_user(email: str, password: str) -> User | None:
    email = email.strip().lower()
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        # Hash anyway to keep timing uniform (don't leak which emails exist).
        bcrypt.checkpw(b"x", bcrypt.hashpw(b"x", bcrypt.gensalt()))
        return None
    if not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        return None
    return User(email=row["email"], role=row["role"], company_id=row["company_id"])
