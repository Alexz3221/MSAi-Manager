#!/usr/bin/env python3
"""Parse Google Cloud MSA notification emails into structured JSON profiles.

Handles both corpus formats:
  A) [Internal MSA Notification]     -> "Field Name\\tValue"   (tab-delimited)
  B) [Account Team MSA Notification] -> "Field Name\\n\\nValue" (blank-line-delimited)
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from google.cloud import storage, bigquery

_gcs = storage.Client()
_bq = bigquery.Client()

ROOT = Path(__file__).resolve().parents[1]
MSA_KEYWORDS_DIR = ROOT / "msa_data" / "msa_keywords_cleaned"

# canonical name -> surface forms seen in the wild
#test commut
SERVICE_ALIASES = {
    "apigee":                   ["apigee", "apigee hybrid", "apigee x", "apigee edge"],
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


DEADLINE_IN_SUBJECT = re.compile(
    r"\b(?:before|by|on|starting|changing)\s+"
    r"([A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})")


def parse_msa_file(bucket_name, blob_name):
    blob = _gcs.bucket(bucket_name).blob(blob_name)
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
        "raw_msa_path": blob_name,
        "format": "account_team" if "Account Team MSA Notification" in text else "internal",
        "sent_date": to_iso(re.sub(r"^\w{3}, ", "", sent.group(1)).split(" at ")[0])
                     if sent else None,
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

BQ_TABLE = "sprinternship-bld-2026.msa_manager.msa_updates"
def write_profile(profile):
    # always write -- an unmatched MSA must be visible, not silently dropped
    if not profile["affected_services"]:
        print(f"WARN: no service matched in {profile['msa_id']}: "
              f"{profile['headline'][:60]!r}", file=sys.stderr)
    errors = _bq.insert_rows_json(BQ_TABLE, [profile])
    if errors:
        print(f"ERROR: failed to write {profile['msa_id']} to BigQuery: {errors}", file=sys.stderr)
    return errors


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 msa_parser.py <bucket-name> [prefix]", file=sys.stderr)
        sys.exit(1)
    bucket_name = sys.argv[1]
    prefix = sys.argv[2] if len(sys.argv) > 2 else ""

    for blob in _gcs.list_blobs(bucket_name, prefix=prefix):
        if not blob.name.endswith(".txt"):
            continue
        p = parse_msa_file(bucket_name, blob.name)
        write_profile(p)
        svc = ", ".join(s["name"] for s in p["affected_services"]) or "!! NONE !!"
        print(f'{p["msa_id"][:24]:26} | {svc:34} | {p["effective_date"] or "-":10} | '
              f'act={"Y" if p["requires_customer_action"] else "n"} | {p["_match_scope"]}')
