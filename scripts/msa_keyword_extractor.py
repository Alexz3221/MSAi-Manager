#!/usr/bin/env python3
"""Parse Google Cloud MSA notification emails into structured JSON profiles.

Handles both corpus formats:
  A) [Internal MSA Notification]     -> "Field Name\\tValue"   (tab-delimited)
  B) [Account Team MSA Notification] -> "Field Name\\n\\nValue" (blank-line-delimited)
"""

import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery, storage

LOGGER = logging.getLogger(__name__)

# canonical name -> surface forms seen in the wild
SERVICE_ALIASES = {
    "apigee":                   ["apigee", "apigee hybrid", "apigee x", "apigee edge"],
    "app engine":               ["app engine", "appengine"],
    "artifact registry":        ["artifact registry"],
    "bigquery":                 ["bigquery", "big query"],
    "bigtable":                 ["bigtable", "cloud bigtable"],
    "cloud armor":              ["cloud armor"],
    "cloud composer":           ["cloud composer", "composer"],
    "cloud functions":          ["cloud functions", "cloud run functions"],
    "cloud interconnect":       ["cloud interconnect", "dedicated interconnect", "partner interconnect"],
    "cloud logging":            ["cloud logging"],
    "cloud nat":                ["cloud nat"],
    "cloud run":                ["cloud run"],
    "cloud sql":                ["cloud sql"],
    "cloud storage":            ["cloud storage", "gcs"],
    "compute engine":           ["compute engine", "gce"],
    "container registry":       ["container registry", "gcr"],
    "dataflow":                 ["dataflow", "cloud dataflow"],
    "dialogflow":               ["dialogflow", "dialogflow es", "dialogflow cx", "conversational agents"],
    "firestore":                ["firestore", "datastore", "cloud datastore"],
    "google kubernetes engine": ["google kubernetes engine", "kubernetes engine", "gke"],
    "iam":                      ["iam", "service account keys", "service account",
                                 "workload identity federation"],
    "identity-aware proxy":     ["identity-aware proxy", "identity aware proxy", "iap"],
    "memorystore":              ["memorystore", "memorystore for redis"],
    "pub/sub":                  ["pub/sub", "pubsub", "cloud pub/sub"],
    "vertex ai":                ["vertex ai", "matching engine", "vector search"],
}


def _alias_pattern(alias):
    body = r"[\s\-]+".join(re.escape(p) for p in alias.split())
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE)


_ALIAS_PATTERNS = [(canon, alias, _alias_pattern(alias))
                   for canon, aliases in SERVICE_ALIASES.items()
                   for alias in aliases]


def normalize(text):
    for a, b in [("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'),
                 ("\u2013", "-"), ("\u2014", "-"), ("\xa0", " ")]:
        text = text.replace(a, b)
    return text


def find_services(region):
    """Return {canonical_name: [surface forms seen]}, longest match wins on overlap."""
    spans = []
    for canon, alias, pat in _ALIAS_PATTERNS:
        for m in pat.finditer(region):
            spans.append((m.start(), m.end(), canon, m.group(0).lower()))
    # drop any match strictly contained inside a longer match
    # ("cloud run" inside "cloud run functions")
    keep = [s for s in spans
            if not any(o is not s and o[0] <= s[0] and s[1] <= o[1] and
                       (o[1] - o[0]) > (s[1] - s[0]) for o in spans)]
    hits = {}
    for _, _, canon, surface in keep:
        hits.setdefault(canon, set()).add(surface)
    return {c: sorted(v) for c, v in sorted(hits.items())}


def get_field(text, label):
    """Read a key/value field in either corpus format."""
    esc = re.escape(label)
    m = re.search(esc + r"[ \t]*\t[ \t]*(.+)", text)          # format A: tab-delimited
    if m:
        return m.group(1).strip()
    m = re.search(esc + r"[ \t]*\n\s*\n[ \t]*(.+)", text)     # format B: blank-line-delimited
    return m.group(1).strip() if m else None


def to_iso(datestr):
    if not datestr:
        return None
    cleaned = re.sub(r"\s+", " ", datestr.strip().rstrip(":."))
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            pass
    return None

def find_distribution_date(text):
    for pattern in DISTRIBUTION_DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return to_iso(match.group(1))
    return None


DEADLINE_IN_SUBJECT = re.compile(
    r"\b(?:before|by|on|starting|changing)\s+"
    r"([A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})")

DISTRIBUTION_DATE_PATTERNS = [
    re.compile(r"scheduled for distribution on\s+([A-Z][a-z]+ \d{1,2},\s*\d{4})"),
    re.compile(r"will receive a version of the following notification on\s+"
                r"([A-Z][a-z]+ \d{1,2},\s*\d{4})"),
]


def parse_msa_file(bucket_name, blob_name):
    blob = storage.Client().bucket(bucket_name).blob(blob_name)
    text = normalize(blob.download_as_text())
    msa_id = Path(blob_name).stem   # same id derivation as before

    # ^ anchors so "Effective Date:" / "End Date:" can't hijack these
    sent = re.search(r"^Date:[ \t]*(.+)$", text, re.M)
    raw_subject = re.search(r"^Subject:[ \t]*(.+)$", text, re.M)

    # the customer-facing subject is the cleaner signal; fall back to the raw header
    subject = get_field(text, "Customer MSA Subject") or (
        raw_subject.group(1).strip() if raw_subject else "")
    subject = re.sub(r"^\[(?:Internal|Account Team) MSA Notification\]\s*", "", subject)

    tag = re.match(r"\[([^\]]+)\]", subject)
    category = tag.group(1) if tag else None
    subject_text = subject[tag.end():].strip() if tag else subject

    # --- services: scope to the subject, fall back to the TLDR ---
    services = find_services(subject)
    scope = "subject"
    if not services:
        tldr = re.search(r"^TLDR:(.+?)(?:\n\n|\Z)", text, re.M | re.DOTALL)
        if tldr:
            services = find_services(tldr.group(1))
            scope = "tldr"
    if not services:
        scope = "none"

    distribution_date = find_distribution_date(text)
    if distribution_date is None:
        LOGGER.warning(
            "No distribution date matched in MSA profile",
            extra={
                "event": "msa_distribution_date_missing",
                "msa_id": msa_id,
            },
        )

    # --- action / deadline: read the real fields, don't guess ---
    requires_action = get_field(text, "Does this message require customers to take action?")
    cost_impact = get_field(
        text, "Could that action taken by customers lead to cost implications?")
    if requires_action is None:  # format A folds both questions into one field
        combined = get_field(
            text,
            "Does this message require customers to take action, and could that "
            "action lead to cost implications?")
        requires_action = cost_impact = combined

    hard_deadline = None
    hd = re.search(r"Is there a hard deadline for customers to complete specific "
                   r"action items\?\s*\n\s*\n\s*(Yes|No)\s*\n\s*\n\s*(.+)", text)
    if hd and hd.group(1) == "Yes":
        hard_deadline = to_iso(hd.group(2))
    if hard_deadline is None:
        m = DEADLINE_IN_SUBJECT.search(subject)
        hard_deadline = to_iso(m.group(1)) if m else None

    reminders = re.findall(r"Reminder \d+:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", text)

    # only format B carries a real list; format A has no customer block at all.
    # NB: "affected projects" in the body is the literal template var
    # ${project.project_id}, never real IDs -- customers are the usable field.
    customers = []
    cm = re.search(r"List of your affected customers:\s*\n(.*?)\n\s*\n\s*Thank you",
                   text, re.DOTALL)
    if cm:
        customers = [c.strip() for c in cm.group(1).strip().split("\n") if c.strip()]

    return {
        "msa_id": msa_id,
        "raw_msa_path": f"gs://{bucket_name}/{blob_name}",
        "format": "account_team" if "Account Team MSA Notification" in text else "internal",
        "sent_date": to_iso(re.sub(r"^\w{3}, ", "", sent.group(1)).split(" at ")[0])
                     if sent else None,
        "distribution_date": distribution_date,
        "category": category,
        "subject": subject,
        "headline": subject_text,
        "bug_id": get_field(text, "MSA Bug ID"),
        "launch_owner": get_field(text, "Launch Owner"),
        "requires_customer_action": requires_action == "Yes",
        "cost_implications": cost_impact == "Yes",
        "effective_date": hard_deadline,
        "reminder_dates": [to_iso(r) for r in reminders],
        "affected_customers": customers,
        "affected_services": [{"name": n, "aliases": a} for n, a in services.items()],
        "_match_scope": scope,
    }

def bigquery_client() -> bigquery.Client:
    project = os.environ.get("BQ_PROJECT_ID") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT"
    )
    return bigquery.Client(project=project) if project else bigquery.Client()


def msa_table_ref(client: bigquery.Client) -> str:
    dataset = os.environ.get("BQ_DATASET", "msa_manager")
    table = os.environ.get("BQ_MSA_UPDATES_TABLE", "msa_updates")
    return f"{client.project}.{dataset}.{table}"


def write_profile(profile, *, client: bigquery.Client | None = None):
    """Idempotently replace one MSA profile using an isolated staging table."""
    # always write -- an unmatched MSA must be visible, not silently dropped
    if not profile["affected_services"]:
        LOGGER.warning(
            "No service matched in MSA profile",
            extra={
                "event": "msa_service_match_missing",
                "msa_id": profile["msa_id"],
            },
        )

    bq_client = client or bigquery_client()
    target_ref = msa_table_ref(bq_client)
    dataset_ref = target_ref.rsplit(".", 1)[0]
    staging_ref = f"{dataset_ref}._msa_updates_staging_{uuid.uuid4().hex}"
    target_schema = bq_client.get_table(target_ref).schema
    load_config = bigquery.LoadJobConfig(
        schema=target_schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    columns = list(profile)
    quoted_columns = ", ".join(f"`{column}`" for column in columns)
    selected_columns = ", ".join(f"staged.`{column}`" for column in columns)
    replace_query = f"""
    BEGIN TRANSACTION;

    DELETE FROM `{target_ref}` AS target
    WHERE target.msa_id IN (
      SELECT staged.msa_id
      FROM `{staging_ref}` AS staged
    );

    INSERT INTO `{target_ref}` ({quoted_columns})
    SELECT {selected_columns}
    FROM `{staging_ref}` AS staged;

    COMMIT TRANSACTION;
    """

    try:
        bq_client.load_table_from_json(
            [profile],
            staging_ref,
            job_config=load_config,
        ).result()
        bq_client.query(replace_query).result()
    except Exception:
        LOGGER.error(
            "Failed to write MSA profile to BigQuery",
            extra={
                "event": "msa_profile_write_failed",
                "msa_id": profile["msa_id"],
                "target_table": target_ref,
            },
            exc_info=True,
        )
        raise
    finally:
        try:
            bq_client.delete_table(staging_ref, not_found_ok=True)
        except Exception:
            LOGGER.warning(
                "Could not remove MSA staging table",
                extra={
                    "event": "msa_staging_cleanup_failed",
                    "staging_table": staging_ref,
                },
                exc_info=True,
            )

    return []


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 msa_parser.py <bucket-name> [prefix]", file=sys.stderr)
        sys.exit(1)
    bucket_name = sys.argv[1]
    prefix = sys.argv[2] if len(sys.argv) > 2 else ""

    for blob in storage.Client().list_blobs(bucket_name, prefix=prefix):
        if not blob.name.endswith(".txt"):
            continue
        p = parse_msa_file(bucket_name, blob.name)
        write_profile(p)
        svc = ", ".join(s["name"] for s in p["affected_services"]) or "!! NONE !!"
        print(f'{p["msa_id"][:24]:26} | {svc:34} | {p["effective_date"] or "-":10} | '
              f'act={"Y" if p["requires_customer_action"] else "n"} | {p["_match_scope"]}')
