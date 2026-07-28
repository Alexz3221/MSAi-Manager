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
import re
from difflib import SequenceMatcher
from collections.abc import Iterator
from contextlib import contextmanager
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
CUSTOMER_DOMAIN_ALIASES = {
    key.strip().casefold(): value.strip()
    for item in os.environ.get("CUSTOMER_DOMAIN_ALIASES", "").split(",")
    if "=" in item
    for key, value in [item.split("=", 1)]
    if key.strip() and value.strip()
}
FUZZY_MIN_SCORE = float(os.environ.get("CUSTOMER_DOMAIN_FUZZY_MIN_SCORE", "0.82"))
FUZZY_MIN_MARGIN = float(os.environ.get("CUSTOMER_DOMAIN_FUZZY_MIN_MARGIN", "0.08"))
FUZZY_MIN_QUERY_LENGTH = int(
    os.environ.get("CUSTOMER_DOMAIN_FUZZY_MIN_QUERY_LENGTH", "8")
)
LEGAL_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
}
BUSINESS_SUFFIXES = LEGAL_SUFFIXES | {
    "group",
    "holding",
    "holdings",
    "prod",
    "pty",
}
GENERIC_SINGLE_ALIAS_TOKENS = {
    "analytics",
    "cloud",
    "consulting",
    "dev",
    "financial",
    "finance",
    "group",
    "iot",
    "logistics",
    "manufacturing",
    "management",
    "media",
    "news",
    "platform",
    "prod",
    "project",
    "retail",
    "services",
    "solutions",
    "systems",
    "traders",
}
DEMO_DOMAIN_ALIASES = {
    "afs": "amberline-financial-services",
    "amberline": "amberline-financial-services",
    "amberlinefinancial": "amberline-financial-services",
    "brcm": "broadcom-corporation",
    "broadcom": "broadcom-corporation",
    "cloudops": "cloud-ops-alpha",
    "coa": "cloud-ops-alpha",
    "cobalt": "cobalt-ridge-manufacturing",
    "cobaltridge": "cobalt-ridge-manufacturing",
    "concordance": "concordance-inc",
    "endeavour": "endeavour-group-limited",
    "endeavourgroup": "endeavour-group-limited",
    "entisys": "entisys-solutions-inc",
    "entisyssolutions": "entisys-solutions-inc",
    "fairfax": "fairfax-media-management-pty-limited",
    "fairfaxmedia": "fairfax-media-management-pty-limited",
    "fmm": "fairfax-media-management-pty-limited",
    "financeplatform": "finance-platform-prod",
    "fpp": "finance-platform-prod",
    "halden": "halden-&-marsh-consulting",
    "haldenmarsh": "halden-&-marsh-consulting",
    "hmc": "halden-&-marsh-consulting",
    "ingram": "ingram-micro-inc",
    "ingrammicro": "ingram-micro-inc",
    "nationwide": "nationwide-news-pty-ltd",
    "nationwidenews": "nationwide-news-pty-ltd",
    "newscorp": "news-corporate-services-inc",
    "newscorporate": "news-corporate-services-inc",
    "northwind": "northwind-traders-llc",
    "northwindtraders": "northwind-traders-llc",
    "pinehollow": "pinehollow-retail-corp",
    "pinehollowretail": "pinehollow-retail-corp",
    "redshaw": "redshaw-logistics-group",
    "redshawlogistics": "redshaw-logistics-group",
    "retailanalytics": "retail-analytics-2026",
    "sada": "sada-systems-llc",
    "sadasystems": "sada-systems-llc",
    "sandbox": "sandbox-project-new",
    "sandboxproject": "sandbox-project-new",
    "smartcity": "smartcity-iot-dev",
    "smartcityiot": "smartcity-iot-dev",
    "sprinternship": "sprinternship-bld-2026",
    "sprinternshipbld": "sprinternship-bld-2026",
    "teg": "teg-pty-ltd",
    "transurban": "transurban-limited",
    "trestle": "trestle-and-co-media",
    "trestlemedia": "trestle-and-co-media",
    "ttec": "ttec-holdings-inc",
    "ttecholdings": "ttec-holdings-inc",
    "vantage": "vantage-point-analytics",
    "vantageanalytics": "vantage-point-analytics",
    "vpa": "vantage-point-analytics",
    "woolies": "woolworths-group-limited",
    "woolworths": "woolworths-group-limited",
}


@dataclass(frozen=True)
class User:
    email: str
    role: str
    company_id: str | None


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


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
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_notice_statuses (
                user_email      TEXT NOT NULL,
                notice_id       TEXT NOT NULL,
                status          TEXT NOT NULL CHECK (status IN ('new', 'in-progress', 'dismissed')),
                updated_at      TEXT NOT NULL,
                PRIMARY KEY (user_email, notice_id),
                FOREIGN KEY (user_email) REFERENCES users(email)
            )
        """)

def get_user_notice_statuses(user_email: str) -> dict[str, str]:
    with _conn() as con:
        rows = con.execute(
            "SELECT notice_id, status FROM user_notice_statuses WHERE user_email = ?",
            (user_email,)
        ).fetchall()
        return {row["notice_id"]: row["status"] for row in rows}

def set_user_notice_status(user_email: str, notice_id: str, status: str) -> None:
    now = dt.datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute("""
            INSERT INTO user_notice_statuses (user_email, notice_id, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_email, notice_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at
        """, (user_email, notice_id, status, now))

def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def _domain_candidates(domain: str) -> list[str]:
    parts = [part for part in domain.split(".") if part]
    candidates = [domain]
    if len(parts) >= 2:
        candidates.append(parts[-2])
    if parts:
        candidates.append(parts[0])
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _without_business_suffixes(tokens: list[str]) -> list[str]:
    stripped = list(tokens)
    while stripped and stripped[-1] in BUSINESS_SUFFIXES:
        stripped.pop()
    return stripped


def _generated_company_aliases(company_id: str, company_name: str) -> set[str]:
    aliases = {_compact(company_id), _compact(company_name)}
    for value in (company_id, company_name):
        tokens = _tokens(value)
        base_tokens = _without_business_suffixes(tokens)
        for candidate_tokens in (tokens, base_tokens):
            if not candidate_tokens:
                continue
            aliases.add("".join(candidate_tokens))
            if candidate_tokens[0] not in GENERIC_SINGLE_ALIAS_TOKENS:
                aliases.add(candidate_tokens[0])
            if len(candidate_tokens) >= 2:
                aliases.add("".join(candidate_tokens[:2]))
                acronym = "".join(token[0] for token in candidate_tokens)
                if 2 <= len(acronym) <= 6:
                    aliases.add(acronym)
    aliases.update(
        alias
        for alias, target_id in DEMO_DOMAIN_ALIASES.items()
        if target_id == company_id
    )
    return {alias for alias in aliases if len(alias) >= 3}


def _demo_alias_index(companies: dict) -> dict[str, str]:
    buckets: dict[str, set[str]] = {}
    for company_id, profile in companies.items():
        for alias in _generated_company_aliases(company_id, profile.company_name):
            buckets.setdefault(alias, set()).add(company_id)
    return {
        alias: next(iter(company_ids))
        for alias, company_ids in buckets.items()
        if len(company_ids) == 1
    }


def _company_match_candidates(company_id: str, company_name: str) -> set[str]:
    return _generated_company_aliases(company_id, company_name)


def _find_company_compact(company_query: str, companies: dict) -> str | None:
    wanted = _compact(company_query)
    if not wanted:
        return None
    hits = []
    for company_id, profile in companies.items():
        candidates = _company_match_candidates(company_id, profile.company_name)
        if any(wanted in candidate or candidate in wanted for candidate in candidates):
            hits.append(company_id)
    unique_hits = list(dict.fromkeys(hits))
    return unique_hits[0] if len(unique_hits) == 1 else None


def _find_company_fuzzy(company_query: str, companies: dict) -> str | None:
    wanted = _compact(company_query)
    if len(wanted) < FUZZY_MIN_QUERY_LENGTH:
        return None

    scores: list[tuple[float, str]] = []
    for company_id, profile in companies.items():
        candidates = _company_match_candidates(company_id, profile.company_name)
        best_score = max(
            (
                SequenceMatcher(None, wanted, candidate).ratio()
                for candidate in candidates
                if candidate
            ),
            default=0.0,
        )
        scores.append((best_score, company_id))

    scores.sort(reverse=True)
    if not scores or scores[0][0] < FUZZY_MIN_SCORE:
        return None
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    if scores[0][0] - second_score < FUZZY_MIN_MARGIN:
        return None
    return scores[0][1]


def _find_company_for_domain(domain: str, companies: dict) -> str | None:
    from msai_core import matching

    for candidate in _domain_candidates(domain):
        alias = (
            CUSTOMER_DOMAIN_ALIASES.get(candidate.casefold())
            or CUSTOMER_DOMAIN_ALIASES.get(_compact(candidate))
        )
        if alias:
            hit = matching.find_company(alias, companies) or _find_company_compact(
                alias, companies
            )
            if hit is not None:
                return hit

    alias_index = _demo_alias_index(companies)
    for candidate in _domain_candidates(domain):
        hit = alias_index.get(_compact(candidate))
        if hit is not None:
            return hit

    for candidate in _domain_candidates(domain):
        hit = matching.find_company(candidate, companies) or _find_company_compact(
            candidate, companies
        )
        if hit is not None:
            return hit

    for candidate in _domain_candidates(domain):
        hit = _find_company_fuzzy(candidate, companies)
        if hit is not None:
            return hit
    return None


def resolve_role_company(email: str) -> tuple[str, str | None]:
    """Decide role + company from the email domain. Server-side only.

    Customer company_id resolution is best-effort against customer_profiles;
    adjust once that table holds real domain-keyed customer data. For the demo,
    CUSTOMER_DOMAIN_ALIASES can map a domain or domain label to a company query,
    and a final high-confidence fuzzy pass catches obvious demo typos.
    """
    domain = _domain(email)
    if domain in INTERNAL_DOMAINS:
        return "internal", None
    # customer: map domain/domain label -> a company in customer_profiles
    try:
        from msai_core import matching
        profiles = matching.load_customer_profiles()
        hit = _find_company_for_domain(domain, profiles)
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
                (
                    email,
                    pw_hash,
                    role,
                    company_id,
                    dt.datetime.now(dt.UTC).isoformat(),
                ),
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
    role, company_id = resolve_role_company(email)
    if role != row["role"] or company_id != row["company_id"]:
        with _conn() as con:
            con.execute(
                "UPDATE users SET role = ?, company_id = ? WHERE email = ?",
                (role, company_id, email),
            )
    return User(email=row["email"], role=role, company_id=company_id)
