# MSAi Manager

MSAi Manager matches customer Google Cloud usage with relevant Mandatory
Service Announcement (MSA) updates. The current app exposes a browser feed,
JSON API endpoints, and the John conversational advisor.

- GitHub: <https://github.com/Alexz3221/MSAi-Manager>
- Cloud Run: <https://msai-manager-1053168925742.europe-west1.run.app>
- Health check: <https://msai-manager-1053168925742.europe-west1.run.app/health>

## Current Shape

- The web app runs on Cloud Run.
- Customer and MSA profiles are stored in BigQuery.
- Raw MSA text and customer-profile exports live in Cloud Storage.
- Pub/Sub triggers the current MSA/customer ingestion path.
- John calls Gemini through Vertex AI and keeps temporary in-memory sessions.
- Logs are written as structured JSON for Cloud Logging.

Primary BigQuery tables:

```text
sprinternship-bld-2026.msa_manager.customer_profiles
sprinternship-bld-2026.msa_manager.msa_updates
sprinternship-bld-2026.msa_dataset.msa_daily_queue
```

## Repository Guide

```text
src/msai_core/                    shared BigQuery and matching code
services/web/                     Cloud Run dashboard and API
services/john/john_agent/         John conversational advisor
scripts/                          ingestion, asset, and notification commands
sql/                              demo and warehouse SQL
tests/                            unit and request-level tests
app.py                            Cloud Run entry point
```

The root `Dockerfile` installs the web, John, and script requirements so the
same image can serve the app or run Cloud Run Jobs.

## Local Start

Python 3.12 is recommended.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python app.py
```

Bash:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
cp -n .env.example .env
python app.py
```

Open <http://localhost:8080>.

Local BigQuery, Cloud Storage, Vertex AI, and Cloud Asset Inventory calls use
Application Default Credentials.

## Configuration

Copy `.env.example` to `.env` for local development. Cloud Run uses its own
environment variables.

Important settings:

```text
DATA_SOURCE=bigquery
GOOGLE_CLOUD_PROJECT=sprinternship-bld-2026
BQ_DATASET=msa_manager
BQ_CUSTOMERS_TABLE=customer_profiles
BQ_MSA_UPDATES_TABLE=msa_updates
BQ_QUEUE_DATASET=msa_dataset
BQ_DAILY_QUEUE_TABLE=msa_daily_queue
MSA_DATA_BUCKET=
CUSTOMER_DATA_BUCKET=
JOHN_ENABLED=true
JOHN_RATE_LIMIT_PER_CLIENT=25
JOHN_RATE_LIMIT_GLOBAL=300
LOG_LEVEL=INFO
```

Set `JOHN_ENABLED=false` to mark John offline without taking down the feed.
Disabled John requests return HTTP 503 before Vertex AI is called.

## Logging

Application logs are emitted as one JSON object per line. Useful fields include:

```text
severity
message
event
service
environment
path
trace
```

Good Cloud Logging filters:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="msai-manager"
jsonPayload.event="request_error"
```

```text
resource.type="cloud_run_revision"
resource.labels.service_name="msai-manager"
severity>=ERROR
```

For the `app-prod` logging bucket, keep the sink focused on Cloud Run app logs:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="msai-manager"
(
  log_id("run.googleapis.com/stdout")
  OR log_id("run.googleapis.com/stderr")
  OR log_id("run.googleapis.com/requests")
)
```

## Scripts

Run scripts as modules from the repository root:

```powershell
python -m scripts.service_pull --help
python -m scripts.combine_and_send --help
```

Common jobs:

- `scripts.service_pull` exports customer Cloud Asset Inventory service usage to
  Cloud Storage.
- `scripts.asset_checker` normalizes customer-profile exports into BigQuery.
- `scripts.msa_keyword_extractor` parses raw MSA text into BigQuery profiles.
- `scripts.combine_and_send` builds notification previews and can send queued
  daily MSA emails when explicitly run with `--send`.

## John

John is available through `POST /api/john` and the browser UI. The deployed
service account needs Vertex AI User permissions. Sessions are temporary and can
reset when Cloud Run replaces an instance, so John should be treated as a
prototype advisor rather than durable chat storage.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `/` | Browser feed and John UI |
| `/health` | Basic health check |
| `/api/companies` | Customer list |
| `/api/services` | Service list |
| `/api/feed` | Filterable MSA feed |
| `/api/john` | John chat endpoint |

Example feed filter:

```text
/api/feed?company=apple&service=bigquery&requires_action=true
```

## Tests

```powershell
python -m unittest discover -s tests
```
