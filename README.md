# MSAi Manager

> **Mock draft:** This README is a lightweight outline of the current prototype
> and possible Google Cloud services. Items marked *possible* are planning ideas,
> not implemented features.

MSAi Manager is a prototype for matching customer Google Cloud usage with
relevant Mandatory Service Announcement (MSA) updates. The current app exposes
a filterable web feed and can prepare notification email previews.

- GitHub: <https://github.com/Alexz3221/MSAi-Manager>
- Cloud Run: <https://msai-manager-1053168925742.europe-west1.run.app>
- Health check: <https://msai-manager-1053168925742.europe-west1.run.app/health>

## Prototype snapshot

- The web app is deployed on Cloud Run.
- Customer and MSA profiles are read from BigQuery.
- BigQuery project: `sprinternship-bld-2026`
- Dataset: `msa_manager`
- Canonical tables: `msa_manager.customer_profiles` and
  `msa_manager.msa_updates`; delivery queue: `msa_dataset.msa_daily_queue`
- `customer_profiles` stores `account`, `client_id`, and `active_services`; the
  application converts each service into its matching profile representation.
- The tables may be empty during development, in which case the feed correctly
  returns zero results.
- Raw MSA text and customer-profile exports live in Cloud Storage.

## Draft service outline

| Service | Possible role | Status |
| --- | --- | --- |
| Cloud Run | Host the web UI and API | In use |
| BigQuery | Store cleaned customer and MSA profiles | In use |
| Cloud Asset Inventory | Discover services used by customer projects | Export script ready for a job |
| Cloud Storage | Supply raw MSA files and store customer-profile exports | In use |
| Secret Manager | Store SMTP or external-service credentials | Possible |
| Pub/Sub | Trigger MSA parsing when new Cloud Storage objects arrive | In use |
| Cloud Scheduler | Run periodic imports and notification checks | Possible |
| Cloud Logging / Monitoring | Centralize logs, errors, and service health | Possible |
| Email provider or SMTP relay | Deliver reviewed customer notifications | Possible |


Cloud Storage and Pub/Sub support the current MSA ingestion path. Cloud
Scheduler and broader notification automation remain planning ideas.

## Repository guide

```text
src/msai_core/                    shared BigQuery and deterministic matching code
services/web/                     deployed dashboard and JSON API
services/john/john_agent/         John conversational-agent prototype
scripts/                          ingestion, asset, and notification commands
sql/                              local demo and future warehouse schemas
tests/                            unit and request-level tests
app.py                            compatibility entry point used by Cloud Run
```

The web service and John both consume `msai_core`; neither service imports the
other. Each deployable component has its own requirements file. The root
`requirements.txt` installs the complete development toolset, while the root
`Dockerfile` installs the web, John, and script requirements so the same image
can run either the service or a Cloud Run Job.

## Local quick start

Python 3.12 is recommended.

Powershell:
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

The web app and John share the root `.env`; service-specific `.env` files are
not needed. John's ADK prototype uses a local SQLite fixture for its scoped
project/notice join. Build and verify that fixture before starting the
interactive agent:

```powershell
DATA_SOURCE=bigquery python3 -m services.john.john_agent.agent
```

The first two commands do not require Google credentials. The interactive
agent uses Vertex AI and therefore requires Application Default Credentials.

## John in the web app

The deployed `msai-manager` service presents two tools on one URL: the MSA feed
and John. The browser sends John prompts to `POST /api/john`; the Python service
runs the ADK agent and calls Gemini through Vertex AI. Gemini inference runs on
Google-managed Vertex AI infrastructure, not inside the Cloud Run container.

The Cloud Run service account needs the Vertex AI User role:

```powershell
gcloud projects add-iam-policy-binding sprinternship-bld-2026 `
  --member="serviceAccount:1053168925742-compute@developer.gserviceaccount.com" `
  --role="roles/aiplatform.user"
```

John currently uses the packaged demo SQLite fixture and in-memory conversation
sessions. A session can be lost whenever Cloud Run replaces the instance, and
the public endpoint still needs end-user authentication before production use.
The app applies an in-memory sliding-window limit of 25 requests per client
every five minutes and 300 total requests per hour by default. These values are
configurable with the `JOHN_RATE_LIMIT_*` settings in `.env.example`. The
existing GitHub build trigger deploys both tools together.

Set `JOHN_ENABLED=false` to mark John offline and block his endpoint without
taking down the feed.
Changing a Cloud Run environment variable creates a new revision:

```powershell
gcloud run services update msai-manager `
  --project sprinternship-bld-2026 `
  --region europe-west1 `
  --update-env-vars JOHN_ENABLED=false
```

Use the same command with `JOHN_ENABLED=true` to turn John back on. Disabled
requests receive HTTP 503 before a Vertex AI request or rate-limit entry is
created.

## Data-source settings

Copy `.env.example` to `.env` and configure Application Default Credentials.
The application is cloud-only: `DATA_SOURCE` defaults to `bigquery` and rejects
the removed local mode.

Process environment variables can still override the file, for example:

```powershell
$env:DATA_SOURCE = "bigquery"
$env:BQ_PROJECT_ID = "sprinternship-bld-2026"
$env:BQ_DATASET = "msa_manager"
$env:BQ_CUSTOMERS_TABLE = "customer_profiles"
$env:BQ_MSA_UPDATES_TABLE = "msa_updates"
$env:BQ_QUEUE_DATASET = "msa_dataset"
$env:BQ_DAILY_QUEUE_TABLE = "msa_daily_queue"
$env:MSA_DATA_BUCKET = "your-msa-bucket"
python app.py
```

The table-name settings are optional when the defaults are used. The queue has
its own dataset setting because `msa_daily_queue` lives in `msa_dataset`, while
the canonical MSA and customer tables live in `msa_manager`. Google Cloud
client libraries use Application Default
Credentials. Cloud Run receives credentials from its assigned service account;
local development requires separately configured credentials.

`MSA_DATA_BUCKET` resolves legacy `raw_msa_path` values that contain only an
object name. Newly ingested MSA rows store a complete `gs://` URI. The tracked
`.env.example` lists non-secret settings for both services. The
ignored root `.env` is loaded for local development without overriding values
already supplied by the process. Cloud Run therefore continues to use its
configured environment variables. BigQuery tables are populated by external
ingestion pipelines rather than this application.

## Operational commands

Run repository scripts as modules from the repository root so imports and data
paths remain consistent:

```powershell
python -m scripts.service_pull --help
python -m scripts.combine_and_send --help
```

### Cloud Asset customer export

`scripts.service_pull` queries real Cloud Asset Inventory data and fails if
credentials, permissions, scope, or uploads fail. It maps Cloud Asset API names
to matching keywords and uploads one customer-profile text object:

```text
gs://BUCKET/raw_client_data/ACCOUNT.txt
```

The format is consumed directly by `scripts.asset_checker`:

```text
Account: example_customer
Client ID: customer-project-id
Active services:
- bigquery
- cloud storage
```

Run a local export to the bucket shown in the Cloud Console:

```powershell
python -m scripts.service_pull `
  --client-id sprinternship-bld-2026 `
  --account-name example_customer `
  --bucket dummy_client_bucket
```

Email-principal searches also require a scope such as
`--scope projects/sprinternship-bld-2026`. Local runs use Application Default
Credentials. The command loads non-secret defaults from the root `.env`.

For a Cloud Run Job built from the same image as the web service, override the
container command and arguments:

```text
Command: python
Arguments: -m, scripts.service_pull
```

Configure these job environment variables:

```text
CUSTOMER_CLIENT_ID=customer-project-id-or-email
CUSTOMER_ACCOUNT_NAME=stable_output_name
CUSTOMER_DATA_BUCKET=dummy_client_bucket
CUSTOMER_RAW_PREFIX=raw_client_data
GCP_SCOPE=projects/sprinternship-bld-2026  # required only for email lookup
```

The job service account needs Cloud Asset Viewer on every queried scope/project
and Storage Object User on the destination bucket. Object User allows later job
runs to replace the same stable object name. The job exits after the upload, so
it can later be executed manually or scheduled.

### Daily MSA delivery queue

In BigQuery mode, `scripts.combine_and_send` reads exactly one date partition
from `msa_dataset.msa_daily_queue`. It selects only rows whose normalized
`status` is `pending`, `queued`, or `failed`, deduplicates `(msa_id, client_id)`,
and joins `msa_id` to `msa_manager.msa_updates` for the canonical notice
content. The queue must be time-partitioned; the job fails instead of falling
back to a full-table scan.

`processed_at` is treated as the immutable queue-entry and partition timestamp.
The consumer changes only `status`: it claims a row as `processing`, marks it
`sent` after SMTP accepts the message, or marks it `failed` for another run.
The nullable `distribution_date` from `msa_updates` still controls delivery: a
missing date, today's date, or a past date is due immediately, while a future
notification receives preview files but remains queued.

By default the current local date is used. `--as-of` provides a deterministic
date for validation:

```powershell
python -m scripts.combine_and_send --as-of 2026-07-20
python -m scripts.combine_and_send --send --as-of 2026-07-20
python -m scripts.combine_and_send --send --consume-queue `
  --recipient notifications@example.com --as-of 2026-07-20
```

SMTP does not hold a message until a future date; scheduling therefore requires
running this command once per day, such as from an end-of-day Cloud Run Job.
Queue consumption requires `--consume-queue`, `--send`, and at least one
explicit `--recipient`. The explicit list is currently a job-wide destination;
the BigQuery customer profile query does not yet expose per-client addresses.
A process that exits after claiming but before recording success or failure can
leave a row in `processing`, so operational recovery must reset that row to
`failed` before rerunning the same date.

`scripts.asset_checker` moves normalized customer profiles from Cloud Storage
through a BigQuery staging table, and `scripts.msa_keyword_extractor` writes MSA
profiles from Cloud Storage to BigQuery. Notification previews remain the only
repository-local runtime artifacts and are written under ignored `outputs/`.

## Useful endpoints

| Endpoint | Purpose |
| --- | --- |
| `/` | Browser feed |
| `/health` | Basic health check |
| `/api/companies` | Customer list |
| `/api/services` | Service list |
| `/api/feed` | Filterable MSA feed |
| `/api/john` | John chat endpoint |

Example filter:

```text
/api/feed?company=apple&service=bigquery&requires_action=true
```
