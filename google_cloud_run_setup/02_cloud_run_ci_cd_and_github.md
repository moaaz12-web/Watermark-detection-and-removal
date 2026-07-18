# Cloud Run CI/CD and GitHub Setup

This document captures the Cloud Run deployment, GitHub repository alignment, Cloud Build trigger, Artifact Registry image path, and CI/CD fixes used for the `infringementdetection` backend.

For terminology, read [Terms and Mental Model](./00_terms_and_mental_model.md) first.

## Current Deployment Summary

| Item | Value |
|---|---|
| Backend framework | FastAPI |
| Container runtime | Dockerfile `prod` stage |
| App server | Gunicorn with Uvicorn workers |
| Container port | `8000` |
| Cloud Run service | `infringementdetection` |
| Region | `us-central1` |
| Primary observed URL | `https://infringementdetection-834057675927.us-central1.run.app` |
| Alternate observed URL | `https://infringementdetection-4ldyqude6a-uc.a.run.app` |
| GitHub repo used by trigger | `moaaz12-web/infringementdetection` |
| Branch | `master` |
| Trigger branch pattern | `^master$` |
| Artifact Registry repo | `cloud-run-source-deploy` |
| Image path | `us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:$COMMIT_SHA` |

Why these values matter: Cloud Build, Artifact Registry, and Cloud Run must all agree on the same repo, branch, image path, service name, and region. If one of these values points somewhere else, builds can succeed while the live backend does not change.

## Deployment Flow

The expected CI/CD flow is:

1. Push code to GitHub repo `moaaz12-web/infringementdetection` on branch `master`.
2. Cloud Build trigger matches branch pattern `^master$`.
3. Cloud Build builds the Docker image from the Dockerfile.
4. Cloud Build pushes the image to Artifact Registry.
5. Cloud Build updates the existing Cloud Run service with the new image.

Building and pushing the image is not enough. The pipeline must also deploy or update the Cloud Run service.

Plain-English version:

- GitHub stores the code.
- Cloud Build turns the code into a Docker image.
- Artifact Registry stores that image.
- Cloud Run is updated to run that image.

Why the deploy step matters: Artifact Registry can contain many images. Cloud Run only serves the image configured on the active revision. A successful image push does not automatically mean Cloud Run switched to it unless the pipeline runs the update step.

## GitHub Repository Alignment

A repo mismatch caused confusion during setup.

The local `origin` was changed from:

```text
qiansun009/infringementdetection
```

to:

```text
moaaz12-web/infringementdetection
```

because the Cloud Build trigger was configured against:

```text
moaaz12-web/infringementdetection
```

The Cloud Build trigger must watch the same repository and branch that developers push to.

Verify locally with:

```bash
git remote -v
git branch --show-current
```

Expected values:

```text
origin -> moaaz12-web/infringementdetection
branch -> master
```

The trigger branch pattern should be:

```text
^master$
```

Why this mattered: the first deployed service could show old endpoints if Cloud Build was building an older repo, a different fork, or a different branch. Always confirm the build log commit SHA matches the commit you expected to deploy.

## Cloud Run Public Access

The Cloud Run service was initially created as an authenticated or placeholder service.

Public access was enabled by either:

- Selecting **Allow public access** during Cloud Run setup
- Granting `allUsers` the Cloud Run Invoker role

The IAM binding is conceptually:

```text
principal: allUsers
role: roles/run.invoker
service: infringementdetection
```

After this, the public service URLs became reachable without authentication.

Why this mattered: a browser or public frontend cannot call an authenticated Cloud Run service unless it sends Google identity tokens. For a public API behind a public frontend, public invocation is the simpler setup. Application-level auth can still be added inside FastAPI if needed.

## Artifact Registry Image

The image should be pushed to Artifact Registry using this path:

```text
us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:$COMMIT_SHA
```

A previous build failed with an invalid Docker tag like:

```text
///:$COMMIT_SHA
```

That happened because substitution variables were missing or empty.

Safe fixes:

1. Hardcode the full image path in the Cloud Build config.
2. Define all required substitutions correctly.

Required substitutions if using variables:

```text
_AR_HOSTNAME=us-central1-docker.pkg.dev
_AR_PROJECT_ID=project-5909a738-2fdc-451e-ad6
_AR_REPOSITORY=cloud-run-source-deploy
_SERVICE_NAME=infringementdetection
_DEPLOY_REGION=us-central1
_PLATFORM=managed
```

The recommended config below hardcodes the image path to avoid empty substitution issues.

Why hardcoding was acceptable here: during setup, empty substitution variables produced invalid Docker tags. A hardcoded image path is less flexible, but it is easier to inspect and harder to misconfigure for a single-service project.

## Recommended Inline Cloud Build Config

Use this as the inline `cloudbuild.yaml` for the Cloud Build trigger:

```yaml
steps:
  - id: Build image
    name: gcr.io/cloud-builders/docker
    args:
      - build
      - --target
      - prod
      - -t
      - us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:$COMMIT_SHA
      - .

  - id: Push image
    name: gcr.io/cloud-builders/docker
    args:
      - push
      - us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:$COMMIT_SHA

  - id: Deploy to Cloud Run
    name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
    entrypoint: gcloud
    args:
      - run
      - services
      - update
      - infringementdetection
      - --image=us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:$COMMIT_SHA
      - --region=us-central1
      - --platform=managed
      - --quiet

images:
  - us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:$COMMIT_SHA

options:
  logging: CLOUD_LOGGING_ONLY
```

## IAM and Permission Fixes

Several permission issues were fixed during setup.

| Problem | Cause | Fix |
|---|---|---|
| Build logs failed | Cloud Build service account could not write logs | Add Logs Writer role and use `options.logging: CLOUD_LOGGING_ONLY` |
| Artifact push denied | Build service account lacked Artifact Registry permissions | Grant Artifact Registry Writer on the Artifact Registry repo |
| Deploy failed with `run.services.get` | Build/deploy service account lacked Cloud Run permissions | Grant Cloud Run Admin |
| Deploy failed when using runtime service account | Build/deploy service account could not act as service account | Grant Service Account User |
| Fastest temporary fix | Roles were granted to runtime service account during troubleshooting | Prefer a dedicated build/deploy service account long term |

Recommended roles for the build/deploy service account:

```text
roles/run.admin
roles/iam.serviceAccountUser
roles/artifactregistry.writer
roles/logging.logWriter
```

Prefer granting Artifact Registry Writer at the repository level rather than broadly at the project level.

Reasoning behind these roles:

- `roles/artifactregistry.writer` is needed because Cloud Build pushes Docker images.
- `roles/run.admin` is needed because Cloud Build updates the Cloud Run service.
- `roles/iam.serviceAccountUser` is needed when Cloud Build deploys a service that runs as a specific runtime service account.
- `roles/logging.logWriter` is needed so build and runtime logs are visible in Cloud Logging.

## Troubleshooting Notes

### Build Pushed Image But Did Not Deploy

The initial build only built and pushed the container image. Cloud Run continued serving the old revision because no deploy/update command ran.

Fix: add the `gcloud run services update` step to Cloud Build.

### Invalid Docker Tag

Error pattern:

```text
invalid reference format: ///:$COMMIT_SHA
```

Cause: image path substitutions were empty.

Fix: hardcode the full image path, or define all `_AR_*`, `_SERVICE_NAME`, `_DEPLOY_REGION`, and `_PLATFORM` substitutions.

### Artifact Registry Push Denied

Cause: the build service account did not have write access to the Artifact Registry repository.

Fix: grant Artifact Registry Writer on:

```text
cloud-run-source-deploy
```

and use the correct image path:

```text
us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:$COMMIT_SHA
```

### Cloud Run Deploy Permission Error

Error involved:

```text
run.services.get
```

Cause: the account performing deployment could not read/update Cloud Run services.

Fix: grant Cloud Run Admin to the build/deploy service account. If the deployment sets or uses a runtime service account, also grant Service Account User on that runtime service account.

## Verification Steps

After pushing to `master`, verify the deployment with these checks.

1. Open Cloud Build History and confirm:
   - Latest build was triggered by repo `moaaz12-web/infringementdetection`
   - Branch is `master`
   - GitCommit matches the commit that was pushed
   - Build completed successfully
   - Steps include build, push, and Cloud Run update

2. Confirm the image exists in Artifact Registry:

```text
us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/cloud-run-source-deploy/infringementdetection:$COMMIT_SHA
```

3. In Cloud Run, open service `infringementdetection` in `us-central1`.

4. Confirm the latest revision uses the image URL containing the same commit SHA from Cloud Build.

5. Check OpenAPI:

```text
/openapi.json
```

The OpenAPI response should include the expected `/api/v1` routes.

6. Check Swagger UI:

```text
/docs
```
