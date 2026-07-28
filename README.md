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
- Cloud Scheduler runs the customer pull and queue consumer Cloud Run Jobs.
- John calls Gemini through Vertex AI and keeps temporary in-memory sessions.
- Logs are written as structured JSON for Cloud Logging.

Primary BigQuery tables:

```text
sprinternship-bld-2026.msa_manager.customer_profiles
sprinternship-bld-2026.msa_manager.msa_updates
sprinternship-bld-2026.msa_manager.msa_daily_queue
```

## Data Pipeline

Customer data follows this path:

```text
Cloud Scheduler (17:00 UTC)
  -> msai-manager-pull-user-data Cloud Run Job
  -> customer export in Cloud Storage
  -> Pub/Sub notification
  -> scripts.asset_checker
  -> msa_manager.customer_profiles
```

MSA data follows this path:

```text
raw MSA text in Cloud Storage
  -> Pub/Sub notification
  -> scripts.msa_keyword_extractor
  -> msa_manager.msa_updates
```

The browser feed and John both match `customer_profiles` directly against
`msa_updates`. The BigQuery scheduled query `msa_daily_queue_append` appends due
deliveries from `msa_manager.v_msa_daily_queue` into
`msa_manager.msa_daily_queue` at 00:00 UTC. Cloud Scheduler runs
`msai-manager-combine-and-send` at 18:00 UTC with `--send --consume-queue`.
The older `msa_daily_queue_append_canonical` scheduled query still exists, but
is disabled and is not used by the app.

## Demo Auth

Users register or log in with an email address. The app derives role and company
server-side from the email domain:

- `google.com` is treated as internal.
- Customer domains are matched against `customer_profiles`.
- Demo matching accepts generated aliases, curated aliases for the current demo
  customer list, legal/business-suffix-stripped names, and high-confidence fuzzy
  matches.
- Active browser sessions refresh their derived company match on authenticated
  requests.
- Customer users without a matched company get an empty feed and service list.
- John does not accept chat-supplied company names as a substitute for a matched
  session company.
- Email ownership is not verified, so this is demo scoping, not production auth.

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
GOOGLE_CLOUD_LOCATION=global
GOOGLE_GENAI_USE_VERTEXAI=TRUE
BQ_PROJECT_ID=sprinternship-bld-2026
BQ_DATASET=msa_manager
BQ_CUSTOMERS_TABLE=customer_profiles
BQ_CUSTOMERS_STAGING_TABLE=customer_profiles_staging
BQ_MSA_UPDATES_TABLE=msa_updates
BQ_QUEUE_DATASET=msa_manager
BQ_DAILY_QUEUE_TABLE=msa_daily_queue
MSA_DATA_BUCKET=
CUSTOMER_DATA_BUCKET=
JOHN_ENABLED=true
JOHN_RATE_LIMIT_PER_CLIENT=25
JOHN_RATE_LIMIT_CLIENT_WINDOW_SECONDS=300
JOHN_RATE_LIMIT_GLOBAL=300
JOHN_RATE_LIMIT_GLOBAL_WINDOW_SECONDS=3600
CUSTOMER_DOMAIN_ALIASES=
CUSTOMER_DOMAIN_FUZZY_MIN_SCORE=0.82
CUSTOMER_DOMAIN_FUZZY_MIN_MARGIN=0.08
CUSTOMER_DOMAIN_FUZZY_MIN_QUERY_LENGTH=8
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
| `/login` | Login/register page |
| `/api/login` | Login |
| `/api/register` | Register |
| `/api/logout` | Logout |
| `/api/companies` | Customer list |
| `/api/me` | Signed-in user and matched organization |
| `/api/services` | Service list |
| `/api/feed` | Filterable MSA feed |
| `/api/notice-status` | Set a MSA notice's per-user status (new/in-progress/dismissed) |
| `/api/company` | Legacy company feed alias |
| `/api/john` | John chat endpoint |
| `POST /` | Pub/Sub push webhook for GCS text ingestion |

Example feed filter:

```text
/api/feed?company=vantage-point-analytics&service=bigquery&requires_action=true
```

## Tests

```powershell
python -m unittest discover -s tests
```
