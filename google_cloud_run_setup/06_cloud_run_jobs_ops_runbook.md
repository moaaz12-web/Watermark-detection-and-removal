# Cloud Run Jobs Operations Runbook

This runbook is for the next operator or agent that needs to inspect Cloud Run
Jobs, connect to production Cloud SQL, run Alembic, inspect job logs, repin job
images after deploys, and clear stuck background-job rows safely.

It intentionally does not store raw passwords, API secrets, app secret keys, or
tokens. Put those values in a private local shell session, password manager, or
untracked private copy. Do not commit a filled-in copy to git.

Why this matters:

- Cloud Run service/job descriptions can print secrets if you dump the full env.
- A committed Markdown file with production secrets is a long-lived credential
  leak.
- The commands below still give an offline/local agent the exact variable names,
  project IDs, resource names, and safe command shapes needed to operate the
  system.

## Current Production Constants

These are the direct non-secret values used by production operations.

```bash
export GCP_PROJECT_ID="project-5909a738-2fdc-451e-ad6"
export GCP_REGION="us-central1"

export API_SERVICE="infringementdetection"
export API_SERVICE_URL="https://infringementdetection-834057675927.us-central1.run.app"

export CLOUD_SQL_CONNECTION_NAME="project-5909a738-2fdc-451e-ad6:us-central1:infringement"
export POSTGRES_DB="infringement"
export POSTGRES_USER="postgres"

export CLOUD_RUN_JOBS_PROJECT="project-5909a738-2fdc-451e-ad6"
export CLOUD_RUN_JOBS_LOCATION="us-central1"
export SHOPIFY_SYNC_JOB="shopify-sync-job"
export SHOPIFY_BULK_SCAN_JOB="shopify-bulk-scan-job"
export LLM_TERM_CLASSIFICATION_JOB="llm-term-classification-job"

export LOCAL_DB_HOST="127.0.0.1"
export LOCAL_DB_PORT="55432"
```

Secret values that must be supplied privately:

```bash
export POSTGRES_PASSWORD="<paste production postgres password>"
export SECRET_KEY="<paste production backend secret key>"
export SHOPIFY_API_KEY="<paste production Shopify API key>"
export SHOPIFY_API_SECRET="<paste production Shopify API secret>"
export SHOPIFY_APP_AUTOMATION_TOKEN="<paste production automation token>"
export SHOPIFY_TOKEN_ENCRYPTION_KEY="<paste production token encryption key>"
```

If you are running inside this Codex workspace, shell commands normally use the
`rtk` prefix. If your environment does not have `rtk`, remove that prefix.

## Current Observed Runtime State

Observed during the Cloud Run Jobs migration incident on 2026-07-03 UTC.
Re-check before acting, because deploys may have changed this.

API service selected non-secret env:

```text
ENV=production
GOOGLE_CLOUD_PROJECT=project-5909a738-2fdc-451e-ad6
GOOGLE_CLOUD_LOCATION=global
CLOUD_RUN_JOBS_PROJECT=project-5909a738-2fdc-451e-ad6
CLOUD_RUN_JOBS_LOCATION=us-central1
CLOUD_SQL_CONNECTION_NAME=project-5909a738-2fdc-451e-ad6:us-central1:infringement
POSTGRES_DB=infringement
POSTGRES_USER=postgres
DB_POOL_PROFILE=default
DB_POOL_SIZE=2
DB_MAX_OVERFLOW=0
BULK_SCAN_DB_POOL_SIZE=12
BULK_SCAN_DB_MAX_OVERFLOW=2
```

Why `CLOUD_RUN_JOBS_LOCATION` is explicit:

- `GOOGLE_CLOUD_LOCATION` was `global` on the API service.
- Cloud Run Jobs are regional.
- Without explicit `CLOUD_RUN_JOBS_LOCATION=us-central1`, dispatch tried
  `locations/global` and failed with a permission/location error.

Bulk scan Cloud Run Job selected non-secret env after repair:

```text
IMAGE=us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:ca3902661b7ad9425ab85822b48a893975bc456f
ENV=production
GOOGLE_CLOUD_LOCATION=us-central1
CLOUD_SQL_CONNECTION_NAME=project-5909a738-2fdc-451e-ad6:us-central1:infringement
POSTGRES_DB=infringement
POSTGRES_USER=postgres
PROJECT_NAME=infringement-pipeline-backend
DB_POOL_PROFILE=bulk_scan
DB_POOL_SIZE=6
DB_MAX_OVERFLOW=10
BULK_SCAN_DB_POOL_SIZE=24
BULK_SCAN_DB_MAX_OVERFLOW=10
```

Shopify sync Cloud Run Job selected non-secret env observed at the same time:

```text
IMAGE=us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection@sha256:4d89ba3fb1f59ba105686a823569aba9a19301f3be40d4cbe31ca358063f003a
COMMAND=python -m app.jobs.runner
ARGS=shopify-sync
ENV=development
GOOGLE_CLOUD_LOCATION=us-central1
CLOUD_SQL_CONNECTION_NAME=project-5909a738-2fdc-451e-ad6:us-central1:infringement
POSTGRES_DB=infringement
POSTGRES_USER=postgres
PROJECT_NAME=infringement-pipeline-backend
DB_POOL_PROFILE=default
DB_POOL_SIZE=6
DB_MAX_OVERFLOW=10
BULK_SCAN_DB_POOL_SIZE=24
BULK_SCAN_DB_MAX_OVERFLOW=10
```

The `llm-term-classification-job` definition was expected by app config/docs,
but was not found by:

```bash
gcloud run jobs describe "$LLM_TERM_CLASSIFICATION_JOB" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID"
```

Why this is documented: future smoke tests should verify all required job
definitions exist before assuming Cloud Run Jobs migration is complete.

## Authenticate GCloud

Check which account and project are active:

```bash
rtk gcloud auth list
rtk gcloud config list
```

Set the project explicitly:

```bash
rtk gcloud config set project "$GCP_PROJECT_ID"
```

If local Application Default Credentials are missing or expired:

```bash
rtk gcloud auth application-default login
```

Why ADC matters:

- `gcloud run ...` uses your gcloud account.
- `cloud-sql-proxy` usually uses Application Default Credentials.
- They can be different, so check both when Cloud SQL proxy auth fails.

## Push Git Changes With SSH

This workspace's `origin` remote is HTTPS:

```bash
https://github.com/moaaz12-web/infringementdetection.git
```

Non-interactive agents may fail when pushing to that URL with:

```text
fatal: could not read Username for 'https://github.com': No such device or address
```

SSH auth is configured and worked on 2026-07-04. Verify it with:

```bash
rtk ssh -o BatchMode=yes -T git@github.com
```

Expected successful auth message:

```text
Hi moaaz12-web! You've successfully authenticated, but GitHub does not provide shell access.
```

Push `master` without changing the configured remote:

```bash
rtk git push git@github.com:moaaz12-web/infringementdetection.git master
```

Push the current local branch to a same-named remote branch:

```bash
rtk git push git@github.com:moaaz12-web/infringementdetection.git HEAD
```

Push the current local branch to a specific remote branch:

```bash
rtk git push git@github.com:moaaz12-web/infringementdetection.git HEAD:your-branch-name
```

After pushing `master` through SSH, refresh the local `origin/master` tracking
ref so `git status` shows the branch as up to date:

```bash
rtk git fetch git@github.com:moaaz12-web/infringementdetection.git master:refs/remotes/origin/master
rtk git status --short --branch
```

Why this is documented: the GitHub connector and HTTPS remote can be available
in different ways across agent sessions. SSH is the reliable path in this
workspace for pushing to `master` or any feature branch from the terminal.

## Inspect Cloud Run Without Printing Secrets

Do not run broad env dumps in shared logs. Prefer filtered JSON reads.

API service image:

```bash
rtk gcloud run services describe "$API_SERVICE" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --format='value(spec.template.spec.containers[0].image)'
```

Bulk job image and DB pool profile only:

```bash
rtk bash -lc '
gcloud run jobs describe "$SHOPIFY_BULK_SCAN_JOB" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --format=json |
.venv/bin/python -c "
import json, sys
d = json.load(sys.stdin)
c = d[\"spec\"][\"template\"][\"spec\"][\"template\"][\"spec\"][\"containers\"][0]
env = {e.get(\"name\"): e.get(\"value\") for e in c.get(\"env\", [])}
print(\"IMAGE=\" + c[\"image\"])
print(\"DB_POOL_PROFILE=\" + str(env.get(\"DB_POOL_PROFILE\")))
"
'
```

Selected non-secret API env:

```bash
rtk bash -lc '
gcloud run services describe "$API_SERVICE" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --format=json |
.venv/bin/python -c "
import json, sys
keys = {
  \"ENV\", \"PROJECT_NAME\", \"GOOGLE_CLOUD_PROJECT\", \"GOOGLE_CLOUD_LOCATION\",
  \"CLOUD_RUN_JOBS_PROJECT\", \"CLOUD_RUN_JOBS_LOCATION\",
  \"CLOUD_SQL_CONNECTION_NAME\", \"POSTGRES_DB\", \"POSTGRES_USER\",
  \"DB_POOL_PROFILE\", \"DB_POOL_SIZE\", \"DB_MAX_OVERFLOW\",
  \"BULK_SCAN_DB_POOL_SIZE\", \"BULK_SCAN_DB_MAX_OVERFLOW\",
}
d = json.load(sys.stdin)
env = d[\"spec\"][\"template\"][\"spec\"][\"containers\"][0].get(\"env\", [])
vals = {e.get(\"name\"): e.get(\"value\") for e in env if e.get(\"name\") in keys}
for key in sorted(vals):
    print(f\"{key}={vals[key]}\")
"
'
```

Why the filter exists: service and job env can contain `POSTGRES_PASSWORD`,
`SECRET_KEY`, Shopify secrets, and tokens.

## Update API Cloud Run Jobs Dispatch Env

Use this when API dispatch fails with a location like `locations/global`.

```bash
rtk gcloud run services update "$API_SERVICE" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --update-env-vars "CLOUD_RUN_JOBS_PROJECT=$CLOUD_RUN_JOBS_PROJECT,CLOUD_RUN_JOBS_LOCATION=$CLOUD_RUN_JOBS_LOCATION"
```

Verify only the relevant keys:

```bash
rtk bash -lc '
gcloud run services describe "$API_SERVICE" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --format=json |
.venv/bin/python -c "
import json, sys
d = json.load(sys.stdin)
env = {
    e.get(\"name\"): e.get(\"value\")
    for e in d[\"spec\"][\"template\"][\"spec\"][\"containers\"][0].get(\"env\", [])
}
for key in [\"GOOGLE_CLOUD_LOCATION\", \"CLOUD_RUN_JOBS_PROJECT\", \"CLOUD_RUN_JOBS_LOCATION\"]:
    print(f\"{key}={env.get(key)}\")
print(\"REVISION=\" + d[\"status\"][\"latestReadyRevisionName\"])
"
'
```

Why this works: `app/core/config.py` gives `CLOUD_RUN_JOBS_PROJECT` and
`CLOUD_RUN_JOBS_LOCATION` precedence over the generic Google Cloud settings.

## Keep Job Images In Sync With API Deploys

Cloud Run Jobs pin their own image. Deploying the API does not automatically
repin job definitions.

Get the currently serving API image:

```bash
export SERVICE_IMAGE="$(
  gcloud run services describe "$API_SERVICE" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT_ID" \
    --format='value(spec.template.spec.containers[0].image)'
)"
echo "$SERVICE_IMAGE"
```

Update the bulk job to the API image:

```bash
rtk gcloud run jobs update "$SHOPIFY_BULK_SCAN_JOB" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --image "$SERVICE_IMAGE" \
  --update-env-vars "DB_POOL_PROFILE=bulk_scan"
```

Update the sync job to the API image:

```bash
rtk gcloud run jobs update "$SHOPIFY_SYNC_JOB" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --image "$SERVICE_IMAGE" \
  --update-env-vars "DB_POOL_PROFILE=default,ENV=production"
```

Only update the LLM job if it exists:

```bash
if gcloud run jobs describe "$LLM_TERM_CLASSIFICATION_JOB" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
  rtk gcloud run jobs update "$LLM_TERM_CLASSIFICATION_JOB" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT_ID" \
    --image "$SERVICE_IMAGE" \
    --update-env-vars "DB_POOL_PROFILE=default,ENV=production"
else
  echo "Missing job definition: $LLM_TERM_CLASSIFICATION_JOB"
fi
```

Why this matters:

- The API dispatch code and job runner live in the same repo.
- Image skew can break argument contracts or DB assumptions.
- A bad pinned digest caused `ContainerImageImportFailed` before Python started.

## List Jobs And Executions

List job definitions:

```bash
rtk gcloud run jobs list \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID"
```

List recent bulk executions:

```bash
rtk gcloud run jobs executions list \
  --job "$SHOPIFY_BULK_SCAN_JOB" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --limit=10
```

Describe one execution:

```bash
export EXECUTION_NAME="shopify-bulk-scan-job-h52fw"

rtk gcloud run jobs executions describe "$EXECUTION_NAME" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID"
```

Show only status conditions:

```bash
rtk gcloud run jobs executions describe "$EXECUTION_NAME" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --format='yaml(status.conditions,status.failedCount,status.succeededCount,status.runningCount,status.startTime,status.completionTime,status.logUri)'
```

Important conditions:

- `ContainerImageImportFailed`: Cloud Run could not import/pull/convert the
  pinned image. Repin the job to the API service image.
- `Completed=True`: execution completed.
- `Completed=False` with failed count: execution failed before or during app
  code.

## Read Logs

API service errors:

```bash
rtk gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="infringementdetection"
   AND severity>=ERROR' \
  --project "$GCP_PROJECT_ID" \
  --limit=50 \
  --format='value(timestamp,severity,httpRequest.status,httpRequest.requestMethod,httpRequest.requestUrl,textPayload)'
```

API service scan-job traffic:

```bash
rtk gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="infringementdetection"
   AND (textPayload:"bulk" OR textPayload:"Cloud Run" OR textPayload:"scan-jobs" OR httpRequest.requestUrl:"scan-jobs")' \
  --project "$GCP_PROJECT_ID" \
  --limit=80 \
  --format='value(timestamp,severity,httpRequest.status,httpRequest.requestMethod,httpRequest.requestUrl,textPayload)'
```

Bulk job logs:

```bash
rtk gcloud logging read \
  'resource.type="cloud_run_job"
   AND resource.labels.job_name="shopify-bulk-scan-job"' \
  --project "$GCP_PROJECT_ID" \
  --limit=100 \
  --format='value(timestamp,severity,textPayload)'
```

Tracebacks near a time window:

```bash
rtk gcloud logging read \
  'timestamp>="2026-07-03T20:29:40Z"
   AND timestamp<="2026-07-03T20:30:15Z"
   AND textPayload:"Traceback"' \
  --project "$GCP_PROJECT_ID" \
  --limit=20 \
  --format='value(timestamp,severity,resource.type,resource.labels.job_name,textPayload)'
```

Why logs are split this way:

- API logs show dispatch and HTTP errors.
- Cloud Run Job logs show worker/container execution.
- A job can be created by the API but fail before Python starts.

## Connect To Production Cloud SQL

Start the Cloud SQL Auth Proxy in one terminal:

```bash
rtk cloud-sql-proxy --debug-logs "$CLOUD_SQL_CONNECTION_NAME" --port "$LOCAL_DB_PORT"
```

Expected ready lines:

```text
Listening on 127.0.0.1:55432
The proxy has started successfully and is ready for new connections!
```

Stop it with `Ctrl-C` after the operation.

In a second terminal, test the DB connection without printing the password:

```bash
rtk .venv/bin/python - <<'PY'
import os
import psycopg

conn = psycopg.connect(
    host=os.environ.get("LOCAL_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("LOCAL_DB_PORT", "55432")),
    user=os.environ.get("POSTGRES_USER", "postgres"),
    password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ.get("POSTGRES_DB", "infringement"),
)
with conn, conn.cursor() as cur:
    cur.execute("SELECT current_database(), current_user")
    print(cur.fetchone())
PY
```

Do not assume local `.env` contains the production password. During the incident,
the local `.env` password did not authenticate to production, while a previous
operator command-history value did. Always verify with a connection test.

## Run Alembic Against Cloud SQL

Use the proxy and force TCP mode by clearing `CLOUD_SQL_CONNECTION_NAME` for the
local command. This avoids the app trying to use `/cloudsql/...` from your
laptop.

Check current revision:

```bash
POSTGRES_HOST="$LOCAL_DB_HOST" \
POSTGRES_PORT="$LOCAL_DB_PORT" \
POSTGRES_USER="$POSTGRES_USER" \
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
POSTGRES_DB="$POSTGRES_DB" \
CLOUD_SQL_CONNECTION_NAME= \
rtk .venv/bin/python -m alembic current
```

Apply migrations:

```bash
POSTGRES_HOST="$LOCAL_DB_HOST" \
POSTGRES_PORT="$LOCAL_DB_PORT" \
POSTGRES_USER="$POSTGRES_USER" \
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
POSTGRES_DB="$POSTGRES_DB" \
CLOUD_SQL_CONNECTION_NAME= \
rtk .venv/bin/python -m alembic upgrade head
```

Why this exact env shape matters:

- `CLOUD_SQL_CONNECTION_NAME=` disables the Cloud Run socket path locally.
- `POSTGRES_HOST=127.0.0.1` and `POSTGRES_PORT=55432` route through the proxy.
- Alembic uses the same app settings as runtime code.

## Check Job Rows In The Database

Latest bulk scan jobs:

```bash
rtk .venv/bin/python - <<'PY'
import os
import psycopg

conn = psycopg.connect(
    host=os.environ.get("LOCAL_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("LOCAL_DB_PORT", "55432")),
    user=os.environ.get("POSTGRES_USER", "postgres"),
    password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ.get("POSTGRES_DB", "infringement"),
)
with conn, conn.cursor() as cur:
    cur.execute("""
        SELECT id, store_id, status, created_at, started_at, finished_at,
               cloud_run_operation_name, cloud_run_execution_name, error
        FROM shopify_bulk_scan_jobs
        ORDER BY id DESC
        LIMIT 10
    """)
    for row in cur.fetchall():
        print(row)
PY
```

Active bulk scan rows:

```bash
rtk .venv/bin/python - <<'PY'
import os
import psycopg

conn = psycopg.connect(
    host=os.environ.get("LOCAL_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("LOCAL_DB_PORT", "55432")),
    user=os.environ.get("POSTGRES_USER", "postgres"),
    password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ.get("POSTGRES_DB", "infringement"),
)
with conn, conn.cursor() as cur:
    cur.execute("""
        SELECT id, store_id, status, created_at
        FROM shopify_bulk_scan_jobs
        WHERE status IN ('queued', 'running')
        ORDER BY id DESC
    """)
    print(cur.fetchall())
PY
```

## Safely Fail A Stuck Bulk Scan Row

Use this when a Cloud Run execution failed before the worker could claim the DB
row, leaving it `queued`. Keep the update guarded by both `id` and current
`status`.

```bash
export BULK_SCAN_JOB_ID="29"

rtk .venv/bin/python - <<'PY'
import os
import psycopg

job_id = int(os.environ["BULK_SCAN_JOB_ID"])
error_message = (
    "Manually failed after Cloud Run Job container image import failed before "
    "worker startup; safe to retry."
)

conn = psycopg.connect(
    host=os.environ.get("LOCAL_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("LOCAL_DB_PORT", "55432")),
    user=os.environ.get("POSTGRES_USER", "postgres"),
    password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ.get("POSTGRES_DB", "infringement"),
)
with conn, conn.cursor() as cur:
    cur.execute(
        """
        UPDATE shopify_bulk_scan_jobs
        SET status = 'failed',
            error = %s,
            finished_at = NOW() AT TIME ZONE 'UTC',
            heartbeat_at = CASE
                WHEN started_at IS NOT NULL THEN NOW() AT TIME ZONE 'UTC'
                ELSE heartbeat_at
            END
        WHERE id = %s
          AND status = 'queued'
        RETURNING id, store_id, status, created_at, started_at, finished_at,
                  cloud_run_operation_name, cloud_run_execution_name, error
        """,
        (error_message, job_id),
    )
    updated = cur.fetchone()
    conn.commit()
    print(updated or "No row updated; row may already have changed")
PY
```

Verify no active rows remain:

```bash
rtk .venv/bin/python - <<'PY'
import os
import psycopg

conn = psycopg.connect(
    host=os.environ.get("LOCAL_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("LOCAL_DB_PORT", "55432")),
    user=os.environ.get("POSTGRES_USER", "postgres"),
    password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ.get("POSTGRES_DB", "infringement"),
)
with conn, conn.cursor() as cur:
    cur.execute("""
        SELECT id, store_id, status, created_at
        FROM shopify_bulk_scan_jobs
        WHERE status IN ('queued', 'running')
        ORDER BY id DESC
    """)
    print(cur.fetchall())
PY
```

Why this update is guarded:

- It prevents overwriting a job that already started or completed.
- It leaves a clear `error` message in the UI/API.
- It unlocks the one-active-bulk-job capacity guard for a retry.

## Incident Patterns And Fixes

### API Dispatch Fails With `locations/global`

Symptom:

```text
403 Permission denied on 'locations/global'
```

Cause:

- API service had `GOOGLE_CLOUD_LOCATION=global`.
- `CLOUD_RUN_JOBS_LOCATION` was missing.
- Dispatcher resolved Cloud Run Jobs location as `global`.

Fix:

```bash
rtk gcloud run services update "$API_SERVICE" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --update-env-vars "CLOUD_RUN_JOBS_PROJECT=$CLOUD_RUN_JOBS_PROJECT,CLOUD_RUN_JOBS_LOCATION=$CLOUD_RUN_JOBS_LOCATION"
```

### Cloud Run Execution Fails With `ContainerImageImportFailed`

Symptom:

```text
ContainerImageImportFailed
failed to fetch metadata from the registry
failed to convert config file to RiptideContainer
```

Cause:

- Job definition was pinned to an old/bad image digest.
- Cloud Run could not import the image before container startup.
- App code never ran, so the DB row stayed `queued`.

Fix:

```bash
export SERVICE_IMAGE="$(
  gcloud run services describe "$API_SERVICE" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT_ID" \
    --format='value(spec.template.spec.containers[0].image)'
)"

rtk gcloud run jobs update "$SHOPIFY_BULK_SCAN_JOB" \
  --region "$GCP_REGION" \
  --project "$GCP_PROJECT_ID" \
  --image "$SERVICE_IMAGE" \
  --update-env-vars "DB_POOL_PROFILE=bulk_scan"
```

Then fail the stale queued DB row with the guarded SQL update above.

### UI Polls A Negative Temporary Job ID

Symptom:

```text
GET /api/v1/stores/2/scan-jobs/-1783108251812/products
psycopg.errors.NumericValueOutOfRange: integer out of range
```

Cause:

- Frontend uses a temporary negative ID while waiting for the real DB job ID.
- Backend route accepted a Python int too large/small for PostgreSQL `integer`.

This is separate from Cloud Run dispatch. It can appear while the real job
creation request is slow or failing.

Operational handling:

- First check whether the `POST /scan-jobs` request returned `202`, `503`, or
  `500`.
- If the real job row exists and has a Cloud Run operation name, debug Cloud Run.
- If no real row exists, debug API dispatch.

Longer-term code hardening:

- Validate route path IDs with a positive int32 range.
- Avoid polling `/scan-jobs/{job_id}` until the frontend has a real DB job ID.

## Smoke Test Flow

Preferred smoke test:

1. Repin job images to the API service image after deploy.
2. Confirm the API service has `CLOUD_RUN_JOBS_PROJECT` and
   `CLOUD_RUN_JOBS_LOCATION`.
3. Confirm no active bulk rows exist.
4. Start a small bulk scan from the website UI.
5. Check that `POST /scan-jobs` returns `202`.
6. Check Cloud Run executions for `shopify-bulk-scan-job`.
7. Check the DB row moves from `queued` to `running`.
8. Check the row eventually moves to `completed` or `failed` with an error.

Why use the UI first:

- It tests the real API dispatcher path.
- It creates the DB row correctly.
- It passes the runtime args exactly like users will.

Manual `gcloud run jobs execute` is useful for infrastructure debugging, but it
can bypass app-level request validation and capacity checks.

## Quick Command Index

```bash
# Service image
rtk gcloud run services describe "$API_SERVICE" --region "$GCP_REGION" --project "$GCP_PROJECT_ID" --format='value(spec.template.spec.containers[0].image)'

# Job list
rtk gcloud run jobs list --region "$GCP_REGION" --project "$GCP_PROJECT_ID"

# Bulk executions
rtk gcloud run jobs executions list --job "$SHOPIFY_BULK_SCAN_JOB" --region "$GCP_REGION" --project "$GCP_PROJECT_ID" --limit=10

# Describe execution
rtk gcloud run jobs executions describe "$EXECUTION_NAME" --region "$GCP_REGION" --project "$GCP_PROJECT_ID"

# Start Cloud SQL proxy
rtk cloud-sql-proxy --debug-logs "$CLOUD_SQL_CONNECTION_NAME" --port "$LOCAL_DB_PORT"

# Alembic current via proxy
POSTGRES_HOST="$LOCAL_DB_HOST" POSTGRES_PORT="$LOCAL_DB_PORT" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" POSTGRES_DB="$POSTGRES_DB" CLOUD_SQL_CONNECTION_NAME= rtk .venv/bin/python -m alembic current

# Alembic upgrade via proxy
POSTGRES_HOST="$LOCAL_DB_HOST" POSTGRES_PORT="$LOCAL_DB_PORT" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" POSTGRES_DB="$POSTGRES_DB" CLOUD_SQL_CONNECTION_NAME= rtk .venv/bin/python -m alembic upgrade head
```
