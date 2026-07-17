-- Runs as-is in SQLite. For BigQuery: TEXT -> STRING, INTEGER -> INT64,
-- drop PRIMARY KEY, and use CREATE OR REPLACE TABLE. Nothing else changes.

CREATE TABLE notices (
  msa_bug_id     TEXT PRIMARY KEY,
  subject        TEXT NOT NULL,
  product        TEXT NOT NULL,
  category       TEXT NOT NULL,   -- action_required|action_advised|pricing|security
  published_date DATE NOT NULL,
  deadline       DATE,            -- NULL = no hard deadline
  summary        TEXT NOT NULL,
  doc_url        TEXT,
  status         TEXT NOT NULL    -- draft|in_review|published
);

-- One row per targeting rule. A notice with zero rows here matches nobody.
CREATE TABLE msa_targets (
  msa_bug_id    TEXT NOT NULL,
  resource_type TEXT NOT NULL,   -- model_endpoint|machine_type|db_version|runtime|api_version|port_type
  match_kind    TEXT NOT NULL,   -- exact|prefix
  match_value   TEXT NOT NULL
);

CREATE TABLE asset_inventory (
  project_id     TEXT NOT NULL,
  resource_type  TEXT NOT NULL,
  resource_value TEXT NOT NULL,
  req_30d        INTEGER NOT NULL,
  last_seen      DATE NOT NULL
);

CREATE TABLE project_access (
  principal_email TEXT NOT NULL,
  project_id      TEXT NOT NULL,
  role            TEXT NOT NULL
);
