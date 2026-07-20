"""Build John's local demo database from hand-labeled fixtures."""
import sqlite3, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / "services" / "john" / "john_agent" / "msa.db"
DB.unlink(missing_ok=True)
con = sqlite3.connect(DB)
con.executescript((ROOT / "sql" / "john_demo.sql").read_text())

# --- notices: hand-labeled from the generated msa_*.txt set -------------------
notices = [
    # bug_id,      subject, product, category, published, deadline, summary, url, status
    ("b/491203845", "[Action Required] Migrate Cloud SQL for MySQL 5.7 instances before Nov 30, 2026",
     "Cloud SQL", "action_required", "2026-06-25", "2026-11-30",
     "MySQL 5.7 reaches end of support. Instances not upgraded will be auto-upgraded to 8.0 "
     "during an uncontrolled maintenance window.", "go/msa-491203845", "published"),

    ("b/468920541", "[Action Required] Migrate Cloud Functions 1st gen deployments before Mar 31, 2027",
     "Cloud Functions", "action_required", "2026-07-08", "2027-03-31",
     "1st gen Cloud Functions stop accepting new deployments. Redeploy on 2nd gen and validate "
     "concurrency and timeout changes.", "go/msa-468920541", "published"),

    ("b/518664201", "[Action Required] Migrate Vertex AI Matching Engine indexes to Vector Search before Oct 30, 2026",
     "Vertex AI", "action_required", "2026-07-17", "2026-10-30",
     "Legacy Matching Engine index endpoints are retired. Redeploy indexes on Vector Search and "
     "validate recall and latency.", "go/msa-518664201", "published"),

    # No deadline. Tests the ORDER BY.
    ("b/508774096", "[Action Advised] Cloud Interconnect will require MACsec-capable ports for new 100G attachments on Sep 8, 2026",
     "Cloud Interconnect", "action_advised", "2026-07-28", None,
     "New 100G VLAN attachments will be provisioned only on MACsec-capable ports. Existing "
     "attachments are unaffected.", "go/msa-508774096", "published"),

    # No targets at all. Tests the honest-empty case.
    ("b/506341882", "[Action Advised] Cloud Armor preconfigured WAF rules update to ModSecurity CRS 4.0 on Sep 22, 2026",
     "Cloud Armor", "action_advised", "2026-07-15", None,
     "Preconfigured WAF ruleset refreshes to CRS 4.0. Review tuning and exclusions in preview "
     "mode before the new rules activate.", "go/msa-506341882", "published"),

    # Targets something she doesn't run. Tests that the join excludes.
    ("b/459884120", "[Action Required] Migrate Dialogflow ES agents to Conversational Agents before Jun 30, 2027",
     "Dialogflow", "action_required", "2026-07-13", "2027-06-30",
     "Dialogflow ES is deprecated. Migrate agents to Conversational Agents (Dialogflow CX).",
     "go/msa-459884120", "published"),

    # Draft. Must never surface.
    ("b/999000111", "[Action Required] Migrate Cloud SQL for MySQL 8.0 minor versions",
     "Cloud SQL", "action_required", "2026-07-14", "2027-01-31",
     "Draft notice, not yet approved for distribution.", "go/msa-999000111", "draft"),
]
con.executemany("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?)", notices)

targets = [
    ("b/491203845", "db_version",    "exact",  "MYSQL_5_7"),
    ("b/468920541", "runtime",       "prefix", "cloudfunctions-v1"),
    ("b/518664201", "api_version",   "exact",  "matching-engine-v1"),
    ("b/508774096", "port_type",     "exact",  "dedicated-100g"),
    ("b/459884120", "api_version",   "exact",  "dialogflow-es"),
    ("b/999000111", "db_version",    "prefix", "MYSQL_8_0"),
    # b/506341882 (Cloud Armor) intentionally has NO targets.
]
con.executemany("INSERT INTO msa_targets VALUES (?,?,?,?)", targets)

assets = [
    ("msai-prod",    "db_version",  "MYSQL_5_7",              412000, "2026-07-15"),
    ("msai-prod",    "runtime",     "cloudfunctions-v1-py311",  8800, "2026-07-15"),
    ("msai-prod",    "api_version", "matching-engine-v1",      51000, "2026-07-14"),
    ("msai-staging", "db_version",  "MYSQL_5_7",                 900, "2026-07-15"),
    ("msai-staging", "runtime",     "cloudfunctions-v2-py312",  4100, "2026-07-15"),
    ("msai-sandbox", "db_version",  "MYSQL_8_0_35",            15000, "2026-07-15"),
    ("msai-sandbox", "port_type",   "dedicated-100g",              7, "2026-07-10"),
    # Stale: last seen outside the 30-day window, must not match.
    ("msai-archive", "db_version",  "MYSQL_5_7",                  50, "2026-03-01"),
    # Belongs to someone else. Must never appear for usagi@.
    ("other-team",   "db_version",  "MYSQL_5_7",              999999, "2026-07-15"),
]
con.executemany("INSERT INTO asset_inventory VALUES (?,?,?,?,?)", assets)

access = [
    ("usagi@example.com", "msai-prod",    "owner"),
    ("usagi@example.com", "msai-staging", "editor"),
    ("usagi@example.com", "msai-sandbox", "owner"),
    ("usagi@example.com", "msai-archive", "viewer"),
    ("someone@example.com", "other-team", "owner"),
]
con.executemany("INSERT INTO project_access VALUES (?,?,?)", access)

con.commit()
con.close()
print(f"seeded {DB}")
