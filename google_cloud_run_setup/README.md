# Google Cloud Run Backend Deployment Runbook

This folder documents the Google Cloud deployment setup for the infringement detection FastAPI backend.

If you are not familiar with Google Cloud terminology, start with [Terms and Mental Model](./00_terms_and_mental_model.md). It explains the names used in the rest of these docs and why each service exists in the deployment.

The deployment uses:

- Cloud Run for the backend service
- Cloud Run Jobs for background job execution
- Cloud Build for CI/CD from GitHub
- Artifact Registry for Docker images
- Cloud SQL for PostgreSQL
- Google Cloud service accounts and IAM for keyless authentication
- Vertex AI / Agent Platform and Cloud Vision for Google AI features

## Project

```text
project-5909a738-2fdc-451e-ad6
```

## Region

```text
us-central1
```

## Cloud Run Service

```text
infringementdetection
```

## Documents

1. [Terms and Mental Model](./00_terms_and_mental_model.md)
2. [Google Cloud Resources and IAM](./01_google_cloud_resources_and_iam.md)
3. [Cloud Run CI/CD and GitHub](./02_cloud_run_ci_cd_and_github.md)
4. [Database, Runtime Config, and Operations](./03_database_runtime_and_operations.md)
5. [Background Jobs, Redis, and Worker Networking](./04_background_jobs_redis_and_workers.md) - historical pre-cutover notes
6. [Cloud Run Jobs Migration For Background Work](./05_cloud_run_jobs_migration.md)
7. [Cloud Run Jobs Operations Runbook](./06_cloud_run_jobs_ops_runbook.md)

## What Each Service Does

| Service | Plain-English purpose | Why we use it |
|---|---|---|
| Cloud Run | Runs the FastAPI backend as a container | Avoids managing servers and supports autoscaling |
| Cloud Run Jobs | Runs background jobs as run-to-completion containers | Avoids always-on worker cost and keeps execution tied to durable DB job rows |
| Cloud Build | Builds and deploys from GitHub pushes | Gives us CI/CD instead of manual deploys |
| Artifact Registry | Stores Docker images | Cloud Run needs a stored image to deploy |
| Cloud SQL | Hosted PostgreSQL database | Keeps production data outside the container |
| IAM | Permission system | Controls who and what can deploy, read secrets, and access the database |
| Service accounts | Identities for software | Lets Cloud Run and Cloud Build authenticate without JSON key files |
| Secret Manager | Secure place for secrets | Safer than hardcoding passwords in code or logs |

## Why The Setup Is Structured This Way

Cloud Run runs containers, so the backend must be built into a Docker image first. Artifact Registry stores that image. Cloud Build automates the build and deploy steps when code is pushed to GitHub.

The running backend uses a runtime service account. Cloud Build uses a build/deploy service account. Keeping those separate is intentional: the running app should only be able to access runtime resources such as Cloud SQL and Google AI APIs, while the build account handles broader deployment actions.

The database is Cloud SQL, not a database inside the container. Containers can be replaced at any time, so production data must live in a managed database.

Background work now uses Cloud Run Jobs as execution transport. PostgreSQL
remains the durable source of truth for job state, progress, and recovery.

## Current Deployment Shape

GitHub pushes to the configured `master` branch should trigger Cloud Build. The intended pipeline is:

```text
GitHub push -> Cloud Build -> Docker build -> Artifact Registry push -> Cloud Run update
```

Cloud Run should use the attached runtime service account and Cloud SQL
connection, not a Google service account JSON key. Cloud Run Jobs should use the
same application image family and runtime access pattern, with job-specific
commands such as `python -m app.jobs.runner shopify-sync`.

## Cloud Run Jobs For Background Work

The worker pool, Celery, and Redis pieces from the old deployment have been
replaced in the repo by Cloud Run Jobs. The migration record lives in
[Cloud Run Jobs Migration For Background Work](./05_cloud_run_jobs_migration.md).
Operational commands live in
[Cloud Run Jobs Operations Runbook](./06_cloud_run_jobs_ops_runbook.md).

The v1 decisions are:

- three manually provisioned Cloud Run Job definitions (sync, bulk scan, LLM
  term classification), executed automatically by the API per user-created job
- PostgreSQL stays the source of truth; Celery and Redis are removed from the
  runtime path
- concurrency caps are enforced by rejection: one running bulk scan globally
  and one running sync-or-LLM job globally, with new requests rejected with
  `409` while the capacity family is busy — no queueing or automatic promotion
  in v1
- cap checks are serialized with per-family PostgreSQL advisory locks, and a
  global stale sweep runs before every cap evaluation so a dead execution
  cannot block job creation

## Important Follow-Ups

- Keep build/deploy permissions separate from runtime app permissions.
- Use `CLOUD_SQL_CONNECTION_NAME` for Cloud Run database connectivity.
- Do not set `GOOGLE_APPLICATION_CREDENTIALS` in Cloud Run.
- Rotate `POSTGRES_PASSWORD` and `SECRET_KEY` if either was exposed during setup.
