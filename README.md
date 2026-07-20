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
- Structured customer and MSA profiles can be read from local JSON or BigQuery.
- The deployed service uses `DATA_SOURCE=bigquery`.
- BigQuery project: `sprinternship-bld-2026`
- Dataset: `msa_manager`
- Tables: `customer_profiles` and `msa_updates`
- `customer_profiles` stores flat `project`, `service`, and `raw_uri` rows;
  the application groups them into project profiles when reading.
- The tables may be empty during development, in which case the feed correctly
  returns zero results.
- Local fixtures remain in the repository; the deployed Pub/Sub ingestion path
  can read new raw MSA text from Cloud Storage.

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
customer_data/                    local customer fixtures
msa_data/                         local MSA fixtures
app.py                            compatibility entry point used by Cloud Run
```

The web service and John both consume `msai_core`; neither service imports the
other. Each deployable component has its own requirements file. The root
`requirements.txt` installs the complete development toolset, while the root
`Dockerfile` installs the web, John, and script requirements so the same image
can run either the service or a Cloud Run Job.

Data is organized under:

```text
customer_data/raw/
customer_data/customer_keywords_cleaned/
msa_data/raw/
msa_data/msa_keywords_cleaned/
```

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
python -m scripts.seed_john_demo
python -m services.john.john_agent.query
python -m services.john.john_agent.agent
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

Copy `.env.example` to `.env`, then choose `DATA_SOURCE=local` or
`DATA_SOURCE=bigquery`. The example defaults to local JSON. Its BigQuery values
already point to the prototype dataset, so BigQuery mode only requires changing
`DATA_SOURCE` and configuring credentials.

Process environment variables can still override the file, for example:

```powershell
$env:DATA_SOURCE = "bigquery"
$env:BQ_PROJECT_ID = "sprinternship-bld-2026"
$env:BQ_DATASET = "msa_manager"
$env:BQ_CUSTOMERS_TABLE = "customer_profiles"
$env:BQ_MSA_UPDATES_TABLE = "msa_updates"
python app.py
```

`BQ_CUSTOMERS_TABLE` and `BQ_MSA_UPDATES_TABLE` are optional when the default
table names are used. Google Cloud client libraries use Application Default
Credentials. Cloud Run receives credentials from its assigned service account;
local development requires separately configured credentials.

The tracked `.env.example` lists non-secret settings for both services. The
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
credentials, permissions, scope, or uploads fail. It never substitutes mock
data or converts the result into a customer-profile JSON document. With a
bucket configured, it uploads one raw text object:

```text
gs://BUCKET/raw_client_data/ACCOUNT.txt
```

Each line preserves the Cloud Asset Inventory resource name and asset type in
the same raw format consumed by `scripts.asset_checker`:

```text
//storage.googleapis.com/example-bucket storage.googleapis.com/Bucket
```

Run a local export to the bucket shown in the Cloud Console:

```powershell
python -m scripts.service_pull `
  --client-id sprinternship-bld-2026 `
  --account-name example_customer `
  --bucket dummy_client_bucket `
  --no-local-output
```

Email-principal searches also require a scope such as
`--scope projects/sprinternship-bld-2026`. Local runs use Application Default
Credentials. The command loads non-secret defaults from the root `.env`.

For a Cloud Run Job built from the same image as the web service, override the
container command and arguments:

```text
Command: python
Arguments: -m, scripts.service_pull, --no-local-output
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

### Email distribution dates

`scripts.combine_and_send` reads the nullable `distribution_date` DATE column
from `msa_updates`. A missing date, today's date, or a date in the past is due
immediately. Future notifications still receive preview files but are not sent
through SMTP until a run reaches their distribution date.

By default the current local date is used. `--as-of` provides a deterministic
date for validation:

```powershell
python -m scripts.combine_and_send --as-of 2026-07-20
python -m scripts.combine_and_send --send --as-of 2026-07-20
```

SMTP does not hold a message until a future date; scheduling therefore requires
running this command periodically, such as from a daily Cloud Run Job. The
current prototype does not yet persist an already-sent delivery ledger. Do not
schedule unattended daily `--send` executions until durable deduplication is
added, because an overdue notification would otherwise be sent again on the
next run.

`scripts.asset_checker` and `scripts.msa_keyword_extractor` currently perform
work when imported and should only be run intentionally. Local generated output
stays under the root data or ignored `outputs/` directories; `service_pull` can
also write directly to its configured bucket.

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
