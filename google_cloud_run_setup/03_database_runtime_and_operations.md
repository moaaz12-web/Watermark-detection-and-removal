# Database Runtime and Operations

This document records the FastAPI backend runtime database setup for Cloud Run, Cloud SQL connectivity, migrations, brand import, CORS, and common operational checks.

For terminology, read [Terms and Mental Model](./00_terms_and_mental_model.md) first.

## Cloud SQL Database

Production uses Cloud SQL for PostgreSQL.

| Setting | Value |
|---|---|
| Instance connection name | `project-5909a738-2fdc-451e-ad6:us-central1:infringement` |
| Database name | `infringement` |
| Port | `5432` |
| Region | `us-central1` |
| Google Cloud project | `project-5909a738-2fdc-451e-ad6` |

Why Cloud SQL exists here: Cloud Run containers should be treated as replaceable. The database must persist independently of the container, so PostgreSQL lives in Cloud SQL.

## Cloud Run Database Connection

The backend originally connected to Cloud SQL public IP using `POSTGRES_HOST`. That path is brittle for Cloud Run because Cloud Run outbound IPs are not stable unless explicitly routed through static egress. Public IP connectivity caused runtime connection issues.

The correct Cloud Run path is to attach the Cloud SQL instance to the Cloud Run service and connect through the Cloud SQL socket mounted under:

```text
/cloudsql
```

Cloud Run should set:

```text
CLOUD_SQL_CONNECTION_NAME=project-5909a738-2fdc-451e-ad6:us-central1:infringement
```

The backend config supports both Cloud Run and local TCP database access:

- When `CLOUD_SQL_CONNECTION_NAME` is set, `app/core/config.py` builds `DATABASE_URL` with the query parameter `host=/cloudsql/<connection-name>`.
- When `CLOUD_SQL_CONNECTION_NAME` is not set, local TCP access still uses `POSTGRES_HOST` and `POSTGRES_PORT`.

This keeps local development and Cloud Run production behavior separate without requiring different code paths.

Why this change was made: public IP access depends on network allowlists and stable source IPs. Cloud Run does not provide a stable outbound IP by default. The Cloud SQL integration gives Cloud Run a managed database connection that is tied to IAM and the Cloud SQL connection name.

## Required Cloud Run API Environment Variables

Set these environment variables on the backend Cloud Run API service:

```text
ENV=production
GOOGLE_CLOUD_PROJECT=project-5909a738-2fdc-451e-ad6
GOOGLE_CLOUD_LOCATION=us-central1
CLOUD_RUN_JOBS_PROJECT=project-5909a738-2fdc-451e-ad6
CLOUD_RUN_JOBS_LOCATION=us-central1
CLOUD_RUN_SHOPIFY_SYNC_JOB_NAME=shopify-sync-job
CLOUD_RUN_SHOPIFY_BULK_SCAN_JOB_NAME=shopify-bulk-scan-job
CLOUD_RUN_LLM_TERM_CLASSIFICATION_JOB_NAME=llm-term-classification-job
CLOUD_SQL_CONNECTION_NAME=project-5909a738-2fdc-451e-ad6:us-central1:infringement

POSTGRES_USER=<set-from-secret-or-env>
POSTGRES_PASSWORD=<set-from-secret-or-env>
POSTGRES_DB=infringement

SECRET_KEY=<set-from-secret-or-env>

WORKERS=2
IMAGE_OCR_MAX_FILE_BYTES=<configured-limit>
IMAGE_LOGO_MIN_SCORE=<configured-threshold>
```

Do not set `GOOGLE_APPLICATION_CREDENTIALS` in Cloud Run. Cloud Run should use the attached runtime service account through Application Default Credentials.

When deploying or updating the service, make sure the Cloud SQL instance is attached to the Cloud Run service:

```bash
gcloud run services update infringementdetection \
  --region us-central1 \
  --add-cloudsql-instances project-5909a738-2fdc-451e-ad6:us-central1:infringement
```

Avoid printing secret values in terminal logs, CI logs, or documentation.

What these variables do:

- `ENV` tells the app it is running in production mode.
- `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` tell Google SDKs which project and region to use.
- `CLOUD_RUN_JOBS_PROJECT` and `CLOUD_RUN_JOBS_LOCATION` tell the API exactly
  where to execute Cloud Run Jobs. Set them explicitly so dispatch does not
  accidentally use a generic Google location such as `global`.
- `CLOUD_RUN_*_JOB_NAME` values identify the Cloud Run Job definitions the API
  can execute.
- `CLOUD_SQL_CONNECTION_NAME` tells the app to use the Cloud Run Cloud SQL socket path.
- `POSTGRES_*` values identify the database, user, and password.
- `SECRET_KEY` is used by the backend for app-level cryptographic signing or secret-dependent behavior.
- `WORKERS` controls how many Gunicorn worker processes start in the container.
- `IMAGE_OCR_MAX_FILE_BYTES` and `IMAGE_LOGO_MIN_SCORE` tune image-processing behavior.

## Required Cloud Run Jobs Runtime

Cloud Run Jobs should use the same application image as the API service, but
override the container command to run the job runner.

The job definitions need:

- the same database connectivity settings needed to reach Cloud SQL
- `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`
- `LLM_PROTECTED_TERM_MODEL` for protected-term LLM jobs
- the same runtime service account, or an equivalent service account with Cloud
  SQL Client, Secret Manager Secret Accessor, Logs Writer, and Vertex AI User
  permissions

Why this matters: the API service launches Cloud Run Job executions, but the
job definitions own the actual runner command and runtime settings. Updating
only the API service image does not automatically repin existing job
definitions, so keep job image repinning as an explicit deploy step unless it is
automated.

Current runner commands:

```text
python -m app.jobs.runner shopify-sync
python -m app.jobs.runner shopify-bulk-scan
python -m app.jobs.runner llm-classify
python -m app.jobs.runner llm-retry-failed-batches
python -m app.jobs.runner llm-apply
```

## Migrations

Do not assume the production database is already at the repository head. Check
the real migration state first:

```bash
alembic current
```

To apply migrations when needed:

```bash
alembic upgrade head
```

Run migration commands from an environment that can reach Cloud SQL. For
production, prefer a controlled Cloud Run Job or an operator environment using
the Cloud SQL Auth Proxy.

### Phase 3.5 rollout note

Phase 3.5 introduces migration `0039_enforce_shopify_store_account_id.py`.

What it does:

- verifies there are no remaining `shopify_stores.account_id IS NULL` rows
- adds store-account indexes for:
  - `shopify_stores.account_id`
  - `(shopify_stores.account_id, shopify_stores.shop_domain)`
- enforces `shopify_stores.account_id` as non-null

Before running `0039` in production:

1. Run `alembic current` against the real production database.
2. Confirm the deployed backend already includes the Phase 3 account-aware store
   and Shopify OAuth write paths.
3. Confirm new Shopify connect/reconnect flows are writing `account_id`.
4. If any `shopify_stores.account_id IS NULL` rows remain, stop and investigate
   before enforcing the constraint.

Why migrations matter: code and database schema must move together. If the app
expects a table or column that has not been created yet, the deployment can
start but fail at runtime.

## Brand Seed Import

The brand seed import script is:

```text
app/scripts/import_brands_xlsx.py
```

Source spreadsheet:

```text
docs/data/店小秘违禁词.xlsx
```

Import settings:

| Field | Value |
|---|---|
| Sheet | `违禁词` |
| Column | `违禁词` |

Importer command:

```bash
python app/scripts/import_brands_xlsx.py \
  --file docs/data/店小秘违禁词.xlsx \
  --sheet 违禁词 \
  --column 违禁词
```

Final import results:

```text
Processed rows: 5000
Final active brands: 4744

COMMON_WORD: 294
MARKETPLACE_NOISE: 1
NORMAL: 4449
```

The difference between processed rows and final active brands is expected because brand names are normalized and duplicate normalized names are collapsed.

Why this import matters: brand detection depends on database brand data loaded during app startup. If the table is empty or stale, the API may be healthy but detection quality will be wrong.

## Local Import Connectivity

Local imports through the Cloud SQL public IP require the current laptop public IP to be present in Cloud SQL Authorized networks.

This already proved brittle:

```text
Old laptop public IP: 111.92.135.45
New laptop public IP: 111.92.135.156
```

For future imports, prefer one of these paths:

- Cloud Run Job using the attached Cloud SQL connection.
- Cloud SQL Auth Proxy from the operator machine.
- Another controlled environment with stable database access.

Avoid relying on temporary laptop public IP allowlisting for repeated production operations.

Why the preferred options are better:

- A Cloud Run Job runs inside Google Cloud and can use the same Cloud SQL integration as the app.
- Cloud SQL Auth Proxy uses IAM instead of manually allowing a changing laptop IP.
- A controlled environment is easier to audit and repeat than ad hoc local network access.

## Runtime Startup Behavior

On startup, the backend calls:

```python
brand_detector.reload()
```

This loads brand data from the database. If the database connection fails, startup logs may show public IP connection errors or Cloud SQL socket errors, depending on the active configuration.

When database connectivity is broken, health may be degraded and brand detection may not behave correctly. Check Cloud Run logs for database connection failures first.

After the real backend image is deployed, `/openapi.json` should show the expected backend routes, including:

```text
/health
/api/v1/...
```

Why startup logs are important: Cloud Run may mark the container as listening before the application has finished loading brand data. If detection behavior is wrong, check the application startup logs, not only the Cloud Run revision status.

## CORS

CORS is configured in:

```text
app/main.py
```

The production frontend origins allowed are:

```text
https://infringementdetection-frontend.vercel.app
https://www.globalhawksystem.com
```

The trailing slash was removed. Keep the origin exact.

Localhost is not currently allowed unless explicitly added later. If local frontend testing needs browser access to the deployed backend, add the required localhost origin intentionally.

Why CORS exists: CORS is enforced by browsers, not by FastAPI clients like `curl`. The backend must explicitly say which frontend origins are allowed to call it from browser JavaScript.

## Verification

### Health

```bash
curl -sS https://<backend-url>/health
```

### Infrastructure Health

```bash
curl -sS https://<backend-url>/health/infrastructure
```

Confirm the response includes:

```text
db.connected
cloud_run_jobs.configured
```

### Swagger Docs

Open in browser:

```text
https://<backend-url>/docs
```

### OpenAPI Routes

```bash
curl -sS https://<backend-url>/openapi.json
```

Confirm the response includes:

```text
/health
/api/v1
```

### Database Counts

Run SQL from a secure database session without printing credentials:

```sql
select count(*) from brands where is_active = true;
```

Expected active brand count after import:

```text
4744
```

Category counts:

```sql
select category, count(*)
from brands
where is_active = true
group by category
order by category;
```

Expected result:

```text
COMMON_WORD        294
MARKETPLACE_NOISE   1
NORMAL           4449
```

### Migration State

```bash
alembic current
```

Expected:

```text
0007
```

## Troubleshooting

### Cloud Run Cannot Connect to Database

Check that the Cloud SQL instance is attached to the Cloud Run service:

```bash
gcloud run services describe infringementdetection \
  --region us-central1
```

Verify that `CLOUD_SQL_CONNECTION_NAME` is set:

```text
project-5909a738-2fdc-451e-ad6:us-central1:infringement
```

If logs show attempts to connect to a public IP, the service is likely missing `CLOUD_SQL_CONNECTION_NAME` or is running an older image.

If logs show socket path errors, verify that the Cloud SQL instance is attached to the Cloud Run service and that the connection name is exact.

### Public IP Works Locally But Not on Cloud Run

Do not rely on public IP from Cloud Run. Cloud Run outbound IP is not stable by default.

Use the Cloud SQL `/cloudsql` socket path for Cloud Run instead.

### Local Import Fails After Previously Working

The laptop public IP may have changed and may no longer be in Cloud SQL Authorized networks. Prefer Cloud SQL Auth Proxy or a Cloud Run Job instead of repeatedly editing authorized networks.

### `/docs` or `/openapi.json` Does Not Show API Routes

Confirm the deployed service is running the real backend image, not a placeholder image.

Check:

```bash
curl -sS https://<backend-url>/openapi.json
```

Expected route groups include:

```text
/health
/api/v1/...
```

### Brand Detection Looks Empty or Incorrect

Check startup logs for `brand_detector.reload()` failures.

Then verify database counts:

```sql
select count(*) from brands where is_active = true;
```

Expected:

```text
4744
```
