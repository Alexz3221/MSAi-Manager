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
| Cloud Asset Inventory | Discover services used by customer projects | Experimental script |
| Cloud Storage | Supply raw MSA files to the ingestion path | In use |
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
other. Each deployable service has its own requirements file. The root
`requirements.txt` installs the complete development toolset, while the root
`Dockerfile` installs only `services/web/requirements.txt` for the existing
Cloud Run deployment.

Data is organized under:

```text
customer_data/raw/
customer_data/customer_keywords_cleaned/
msa_data/raw/
msa_data/msa_keywords_cleaned/
```

## Local quick start

Python 3.12 is recommended.

```powershell
/// Powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps

$env:DATA_SOURCE = "local"
python app.py
```

```bash
/// Bash (Linux terminal)
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps

export DATA_SOURCE=local
python app.py
```

Open <http://localhost:8080>.

John's ADK prototype uses a local SQLite fixture for its scoped project/notice
join. Build and verify that fixture before starting the interactive agent:

```powershell
python -m scripts.seed_john_demo
python -m services.john.john_agent.query
python -m services.john.john_agent.agent
```

The first two commands do not require Google credentials. The interactive
agent uses Vertex AI and therefore requires Application Default Credentials.

## Data-source settings

Local JSON is the default. For BigQuery:

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

The tracked `.env.example` lists the main settings. The web app reads variables
from the process environment; it does not automatically load `.env`. BigQuery
tables are populated by external ingestion pipelines rather than this
application.

## Operational commands

Run repository scripts as modules from the repository root so imports and data
paths remain consistent:

```powershell
python -m scripts.service_pull --help
python -m scripts.combine_and_send --help
```

`scripts.asset_checker` and `scripts.msa_keyword_extractor` currently perform
work when imported and should only be run intentionally. Generated output stays
under the root data or ignored `outputs/` directories.

## Useful endpoints

| Endpoint | Purpose |
| --- | --- |
| `/` | Browser feed |
| `/health` | Basic health check |
| `/api/companies` | Customer list |
| `/api/services` | Service list |
| `/api/feed` | Filterable MSA feed |

Example filter:

```text
/api/feed?company=apple&service=bigquery&requires_action=true
```
