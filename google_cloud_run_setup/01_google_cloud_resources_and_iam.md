# Google Cloud Resources and IAM

This document records the Google Cloud resources and IAM setup used for the FastAPI backend deployed to Cloud Run.

For terminology, read [Terms and Mental Model](./00_terms_and_mental_model.md) first.

## Project

- Project ID: `project-5909a738-2fdc-451e-ad6`
- Region: `us-central1`
- Cloud Run service: `infringementdetection`
- Frontend CORS origins:
  - `https://infringementdetection-frontend.vercel.app`
  - `https://www.globalhawksystem.com`

Why this matters:

- The project is the boundary for resources, billing, logs, and permissions.
- The region should match between Cloud Run and Cloud SQL to keep the app close to the database.
- The Cloud Run service name is the stable deployment target even though every deploy creates a new revision.
- The frontend CORS origin is the browser origin allowed to call the backend API.

## Enabled APIs

The following Google Cloud APIs were enabled during setup:

- Cloud Run Admin API
- Cloud Build API
- Artifact Registry API
- Cloud SQL Admin API
- Vertex AI / Agent Platform API
- Cloud Vision API
- Secret Manager API
- Service Usage API, as needed for enabling and managing services

Why this was needed: Google Cloud products are disabled by default in many projects. IAM permissions do not matter if the API for that product is disabled. Enabling these APIs makes the services available in the project.

## Artifact Registry

Container images are stored in Artifact Registry.

- Repository: `cloud-run-source-deploy`
- Format: Docker
- Mode: Standard
- Region: `us-central1`

Why this exists: Cloud Run deploys Docker images, not raw source code. Cloud Build creates the image and pushes it to Artifact Registry. Cloud Run then pulls that image when creating a revision.

## Cloud SQL

The backend connects to the Cloud SQL instance using this connection name:

```text
project-5909a738-2fdc-451e-ad6:us-central1:infringement
```

Cloud Run should connect using the Cloud SQL integration. The runtime service account must have Cloud SQL access.

Why this exists: production data must live outside the Cloud Run container. Containers are disposable; Cloud SQL is the managed PostgreSQL database that persists application data.

## Service Accounts

Service accounts are identities for software. They let Cloud Run, Cloud Build, and Google SDKs authenticate without a human logging in.

The important rule in this setup is to separate runtime permissions from deployment permissions.

### Human Developer User

User:

```text
moaaz7151@gmail.com
```

This user was granted `Owner` during setup so the initial configuration could be completed quickly.

This broad human access should be treated as setup-only access. After deployment is stable, reduce it to the minimum roles needed for normal maintenance.

Why this was done: during initial setup, many blocked actions were IAM and service configuration changes. Owner access allowed those setup issues to be fixed without repeatedly asking the project owner for each permission.

### Runtime Service Account

Runtime service account:

```text
infringement@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com
```

This is the application runtime identity used by the Cloud Run service. It should stay limited to permissions the FastAPI app needs while running.

Runtime roles:

- Cloud SQL Client: `roles/cloudsql.client`
- Agent Platform user / Vertex AI User: `roles/aiplatform.user`
- Secret Manager Secret Accessor: `roles/secretmanager.secretAccessor`
- Logs Writer: `roles/logging.logWriter`

No service account JSON key is needed for the runtime service account. Cloud Run uses Application Default Credentials through the metadata server.

Why these roles exist:

- Cloud SQL Client lets the app connect to the PostgreSQL instance through the Cloud SQL integration.
- Agent Platform user / Vertex AI User lets the app call Google AI services.
- Secret Manager Secret Accessor lets the app read secrets if secrets are wired through Secret Manager.
- Logs Writer lets the running app write logs to Cloud Logging.

Why this account should stay limited: if the running backend is ever compromised, the attacker gets whatever permissions the runtime service account has. Runtime permissions should be enough for the app to work, not enough to redesign the whole cloud project.

### Build and Deploy Account

Initial Cloud Build account:

```text
834057675927-compute@developer.gserviceaccount.com
```

The build/deploy account should be separate from the runtime service account.

Build/deploy roles:

- Logs Writer: `roles/logging.logWriter`
- Artifact Registry Writer: `roles/artifactregistry.writer`
- Cloud Run Admin: `roles/run.admin`
- Service Account User: `roles/iam.serviceAccountUser`
- Cloud Build Service Account: `roles/cloudbuild.builds.builder`

During setup, the fastest temporary workaround was granting deploy-related roles to the runtime service account. That is not the preferred long-term model.

Cleaner long-term setup:

- Build/deploy account builds images, pushes to Artifact Registry, and deploys Cloud Run.
- Runtime service account runs the FastAPI app with only runtime permissions.

Why this split matters: deployment needs broad permissions such as pushing images and updating Cloud Run. The app itself does not need those permissions while serving requests.

## Security Notes

Do not grant `Owner` or Project IAM Admin permissions to the runtime service account long-term.

Rotate the database password and `SECRET_KEY` if either was exposed during setup, logs, local shell history, screenshots, or shared messages.

Organization policies such as `iam.disableServiceAccountKeyCreation` can only be changed by organization administrators. If key creation is blocked by policy, use keyless authentication through Cloud Run, Cloud Build, Workload Identity, or user ADC instead of trying to bypass the policy.
