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
- The tables may be empty during development, in which case the feed correctly
  returns zero results.
- Raw text and generated output still use local files; Cloud Storage support has
  not been implemented.

## Draft service outline

| Service | Possible role | Status |
| --- | --- | --- |
| Cloud Run | Host the web UI and API | In use |
| BigQuery | Store cleaned customer and MSA profiles | In use |
| Cloud Asset Inventory | Discover services used by customer projects | Experimental script |
| Cloud Storage | Store raw MSA files, raw customer profiles, and generated previews | Possible |
| Secret Manager | Store SMTP or external-service credentials | Possible |
| Pub/Sub | Trigger parsing or notification jobs when new data arrives | Possible |
| Cloud Scheduler | Run periodic imports and notification checks | Possible |
| Cloud Logging / Monitoring | Centralize logs, errors, and service health | Possible |
| Email provider or SMTP relay | Deliver reviewed customer notifications | Possible |


Cloud Storage could sit behind both raw-data inputs, while Pub/Sub or Cloud
Scheduler could trigger parsing and notification steps. This is only a draft
architecture; the current code does not yet connect those services.

## Repository guide

| File | Purpose |
| --- | --- |
| `app.py` | Web server, UI, and JSON endpoints |
| `msa_chatbot.py` | Matching, filtering, and command-line lookup |
| `bigquery_data.py` | BigQuery data reader |
| `seed_bigquery.py` | Loads local cleaned JSON into existing BigQuery tables |
| `msa_keyword_extractor.py` | Converts raw MSA text into cleaned JSON |
| `service_pull.py` | Experiments with Cloud Asset Inventory customer profiles |
| `combine_and_send.py` | Builds email previews and optionally uses SMTP |

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
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

$env:DATA_SOURCE = "local"
python app.py
```

Open <http://localhost:8080>.

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
from the process environment; it does not automatically load `.env`.

## Seed the BigQuery prototype

The dataset and tables must already exist. To replace their contents with the
current local cleaned JSON:

```powershell
python seed_bigquery.py --replace
```

Omit `--replace` only when rows should be appended. Review local JSON changes
before using replacement mode because it truncates both target tables.

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
