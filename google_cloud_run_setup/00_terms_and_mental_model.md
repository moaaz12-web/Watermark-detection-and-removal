# Terms and Mental Model

This page explains the Google Cloud terms used in this runbook. Read this first if Cloud Run, IAM, service accounts, or Cloud Build are unfamiliar.

## Big Picture

The backend deployment has four main pieces:

```text
GitHub repo
  -> Cloud Build trigger
  -> Docker image in Artifact Registry
  -> Cloud Run service
  -> Cloud SQL database
```

In plain English:

1. Code is pushed to GitHub.
2. Google Cloud notices the push through a Cloud Build trigger.
3. Cloud Build builds the FastAPI app into a Docker image.
4. The image is stored in Artifact Registry.
5. Cloud Run starts a new revision of the backend using that image.
6. The backend connects to Cloud SQL for PostgreSQL.

## Core Google Cloud Terms

### Project

A Google Cloud project is the container for all cloud resources: Cloud Run, Cloud SQL, IAM permissions, billing, logs, and APIs.

For this backend:

```text
project-5909a738-2fdc-451e-ad6
```

Why it matters: most permissions and resources are scoped to a project. If the wrong project is selected in the console, settings may appear missing or deployments may go to the wrong place.

### Region

A region is the physical Google Cloud location where resources run.

For this backend:

```text
us-central1
```

Why it matters: keeping Cloud Run and Cloud SQL in the same region reduces latency and avoids unnecessary cross-region networking complexity.

### API

In Google Cloud, an API is a product interface that must be enabled before a service can be used. For example, Cloud Run needs the Cloud Run API, and Cloud Build needs the Cloud Build API.

Why it matters: if an API is disabled, IAM roles alone are not enough. The feature still will not work until the API is enabled.

### Cloud Run

Cloud Run runs containers without managing servers. You give it a Docker image, environment variables, and permissions. Google handles starting, stopping, and scaling instances.

Why we use it: it is a good fit for a FastAPI backend because it can scale down to zero, deploy from containers, and use Google service accounts without a JSON key file.

### Service

A Cloud Run service is the public or private HTTP endpoint for the app.

For this backend:

```text
infringementdetection
```

Why it matters: the service is the stable deployment target. Every deploy creates a new revision under the same service.

### Worker Pool

A Cloud Run Worker Pool runs containers that do background work and do not need
to serve HTTP traffic.

For this backend, the Worker Pool runs the Celery worker process that consumes
Redis-backed queues such as:

```text
shopify-sync
llm-term-classification
```

Why it matters: the FastAPI Cloud Run service handles requests and enqueues
jobs, while the Worker Pool consumes those jobs outside the request lifecycle.
When worker code changes, deploy a new container image to the Worker Pool as
well as the API service.

### Revision

A revision is a specific deployed version of a Cloud Run service or Worker
Pool. Every time the image, command, arguments, or config changes, Cloud Run
creates a new revision.

Why it matters: if something breaks after deploy, check which revision is active.
Cloud Run services can route traffic between revisions or roll back to an older
one. Worker Pools should also be checked for which revision is currently
running the background worker command.

### Container

A container is the packaged app plus its dependencies. The Dockerfile describes how the container image is built.

Why it matters: Cloud Run does not run the repo directly. It runs a built image created from the repo.

### Docker Image

A Docker image is the versioned build artifact produced by `docker build`.

Why it matters: the image is what Cloud Run deploys. If Cloud Build builds an image but Cloud Run still uses an old image, the new code will not be live.

### Artifact Registry

Artifact Registry stores built Docker images.

For this backend:

```text
cloud-run-source-deploy
```

Why it matters: Cloud Build needs permission to push images here, and Cloud Run needs to read the image from here.

### Cloud Build

Cloud Build is Google Cloud's build/deploy automation service. It can fetch code from GitHub, run Docker builds, push images, and run `gcloud` deployment commands.

Why we use it: it gives us CI/CD, so pushing to GitHub can automatically build and deploy the backend.

### Trigger

A Cloud Build trigger is a rule that starts a build when something happens, usually a push to a GitHub branch.

For this backend, the trigger watches:

```text
repo: moaaz12-web/infringementdetection
branch: master
```

Why it matters: if the trigger watches the wrong repo or branch, pushes will not deploy the app.

### CI/CD

CI/CD means Continuous Integration and Continuous Deployment.

For this project, it means:

```text
push to GitHub -> build image -> push image -> update Cloud Run
```

Why it matters: after CI/CD is correct, developers should not manually change the Cloud Run image URL for every deploy. The trigger should do it.

## IAM and Identity Terms

### IAM

IAM means Identity and Access Management. It controls who or what can do things in Google Cloud.

Examples:

- Who can edit Cloud Run services.
- Which service account can read Cloud SQL.
- Which build account can push Docker images.

Why it matters: most deployment failures we saw were IAM failures.

### Principal

A principal is an identity that can receive permissions. It can be a person, a service account, or a public group like `allUsers`.

Examples:

```text
moaaz7151@gmail.com
infringement@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com
allUsers
```

### Role

A role is a bundle of permissions.

Example:

```text
roles/run.admin
```

This role includes permissions needed to manage Cloud Run services.

Why it matters: Google Cloud usually grants roles, not individual permissions.

### Service Account

A service account is an identity for software, not a human. Cloud Run and Cloud Build use service accounts to access other Google Cloud resources.

Why it matters: the backend should authenticate as a service account when it talks to Cloud SQL, Vertex AI / Agent Platform, Vision, or Secret Manager.

### Runtime Service Account

The runtime service account is the identity used by the running backend app.

For this backend:

```text
infringement@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com
```

Why it matters: this account should have only app runtime permissions, such as Cloud SQL Client and Vertex AI / Agent Platform access.

### Build/Deploy Service Account

The build/deploy service account is the identity used by Cloud Build to build images and update Cloud Run.

Why it matters: this account needs deployment permissions, such as Artifact Registry Writer and Cloud Run Admin. Those permissions are broader than what the running app needs.

### Least Privilege

Least privilege means giving each identity only the permissions it needs.

Why it matters: if the app is compromised, a limited runtime service account reduces what an attacker can do.

### Service Account Key JSON

A service account key JSON file is a downloadable private key for a service account.

Why we avoid it: key files can be copied, leaked, committed, or reused outside Google Cloud. Cloud Run can use keyless authentication, so a key file is not needed.

### Application Default Credentials

Application Default Credentials, often called ADC, is the standard way Google libraries find credentials.

In Cloud Run, ADC comes from the attached service account automatically.

Why it matters: this lets Google SDKs work without setting `GOOGLE_APPLICATION_CREDENTIALS` or uploading a key file.

## Database Terms

### Cloud SQL

Cloud SQL is Google's managed database service. We use it for PostgreSQL.

Why we use it: Google manages the database server operations, backups, patching, and availability features.

### Cloud SQL Connection Name

The connection name uniquely identifies a Cloud SQL instance:

```text
project-5909a738-2fdc-451e-ad6:us-central1:infringement
```

Why it matters: Cloud Run uses this exact name to mount the Cloud SQL connection under `/cloudsql`.

### Authorized Networks

Authorized networks are IP ranges allowed to connect to a Cloud SQL public IP.

Why this was painful: laptop public IPs can change, so local access may break after it previously worked.

### Cloud SQL Auth Proxy

The Cloud SQL Auth Proxy is a local tool that creates a secure tunnel to Cloud SQL using Google IAM credentials.

Why it helps: it avoids repeatedly adding changing laptop IPs to Authorized networks.

### Migration

A migration is a database schema change tracked in code. This project uses Alembic migrations.

Why it matters: deploying code without applying migrations can make the app fail if it expects tables or columns that do not exist yet.

## Web Terms

### CORS

CORS is a browser security rule. It controls whether frontend JavaScript from one origin can call an API on another origin.

Frontend origin:

```text
https://infringementdetection-frontend.vercel.app
```

Backend origin:

```text
https://infringementdetection-834057675927.us-central1.run.app
```

Why it matters: even if the backend is public, the browser can block frontend requests unless the backend explicitly allows the frontend origin.

### Public Access

Public access means unauthenticated users can call the Cloud Run service.

Why it matters: a public frontend usually needs a public backend API, unless you build an authentication layer between them.

### Cloud Run Invoker

Cloud Run Invoker is the role that allows calling a Cloud Run service.

When public access is enabled, Google grants this role to:

```text
allUsers
```

Why it matters: without this permission, users may see authentication errors when opening the backend URL.

## How to Debug Problems

When something breaks, identify which layer failed:

| Symptom | Likely layer |
|---|---|
| Push does not start a build | GitHub trigger |
| Docker build fails | Dockerfile or dependencies |
| Image push is denied | Artifact Registry IAM |
| Build succeeds but old code is live | Missing Cloud Run deploy step |
| Cloud Run deploy is denied | Cloud Run IAM or Service Account User IAM |
| App starts but database fails | Cloud SQL connection or DB env vars |
| Frontend browser call is blocked | CORS |
| `/docs` shows old endpoints | Old image or wrong repo/branch deployed |
