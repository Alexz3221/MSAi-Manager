# MSAi-Manager

MSAi Manager matches customer Google Cloud services to relevant MSA updates,
displays the results in a filterable feed, and prepares notification emails.

## Data sources

The application uses local JSON by default. To read the `customer_profiles` and
`msa_updates` tables from BigQuery, set:

```text
DATA_SOURCE=bigquery
BQ_PROJECT_ID=sprinternship-bld-2026
BQ_DATASET=msa_manager
BQ_CUSTOMERS_TABLE=customer_profiles
BQ_MSA_UPDATES_TABLE=msa_updates
```

`BQ_CUSTOMERS_TABLE` and `BQ_MSA_UPDATES_TABLE` are optional when the tables use
the default names shown above. On Cloud Run, use a service account with BigQuery
Job User and BigQuery Data Viewer access; Application Default Credentials are
used automatically.

Install dependencies and populate empty tables from the checked-in sample JSON:

```powershell
python -m pip install -r requirements.txt
python seed_bigquery.py --replace
```

The `--replace` flag makes repeat demo loads deterministic by replacing existing
rows. Omit it when you intentionally want to append. The seed command always
uses the checked-in JSON files as its source.

Run the web application locally with:

```powershell
python app.py
```
