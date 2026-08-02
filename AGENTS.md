@/home/maaz/.codex/RTK.md

# Watermark System Context Index

Before working on this repository, read the authoritative agent guide:

[Hybrid Watermark System Agent Guide](docs/productionization/00-hybrid-watermark-system-agent-guide.md)

Always read its
[current-state and freshness rules](docs/productionization/00-hybrid-watermark-system-agent-guide.md#2-current-state-and-freshness-rules)
and then jump to the relevant section:

| Task | Guide section or runbook |
| --- | --- |
| Understand repositories and ownership | [Repository map](docs/productionization/00-hybrid-watermark-system-agent-guide.md#3-repository-map-and-ownership) |
| Understand the end-to-end flow | [Whole-system architecture](docs/productionization/00-hybrid-watermark-system-agent-guide.md#4-whole-system-architecture) |
| Find GCP services or connect safely | [GCP service inventory](docs/productionization/00-hybrid-watermark-system-agent-guide.md#6-gcp-service-inventory) |
| Find AWS services or connect safely | [AWS service inventory](docs/productionization/00-hybrid-watermark-system-agent-guide.md#7-aws-service-inventory) |
| Understand authentication and trust | [Authentication and connection paths](docs/productionization/00-hybrid-watermark-system-agent-guide.md#8-authentication-and-connection-paths) |
| Understand FastAPI routes and states | [Public FastAPI API](docs/productionization/00-hybrid-watermark-system-agent-guide.md#9-public-fastapi-watermark-api) and [job lifecycle](docs/productionization/00-hybrid-watermark-system-agent-guide.md#10-job-lifecycle) |
| Build or deploy code/infrastructure | [Build and deployment paths](docs/productionization/00-hybrid-watermark-system-agent-guide.md#12-build-deploy-and-infrastructure-paths) |
| Build, deploy, or accept an AWS GPU worker image | [AWS worker image deployment and GPU acceptance runbook](docs/productionization/aws-worker-image-deployment-and-gpu-acceptance-runbook.md) |
| Inspect logs or diagnose a job | [Safe observability](docs/productionization/00-hybrid-watermark-system-agent-guide.md#13-safe-observability) |
| Analyze a completed job's timings from its `wm_...` ID | [Job metrics and timing-analysis runbook](docs/productionization/15-job-metrics-and-timing-analysis-runbook.md) |
| Enable or disable normal operation | [Operational modes](docs/productionization/00-hybrid-watermark-system-agent-guide.md#14-operational-modes-and-cost-behavior), then [operational-mode runbook](docs/productionization/09-operational-mode-switch-runbook.md) |
| Verify no GPU is active | [Safety and zero-GPU verification](docs/productionization/00-hybrid-watermark-system-agent-guide.md#15-safety-and-zero-gpu-verification) |
| Work on 10,000-image scale or performance | [Performance and scale facts](docs/productionization/00-hybrid-watermark-system-agent-guide.md#16-performance-and-scale-facts) |
| Find the exact owner/runbook for any task | [Task-routing index](docs/productionization/00-hybrid-watermark-system-agent-guide.md#17-task-routing-index) |
| Decide whether an older document is authoritative | [Document authority](docs/productionization/00-hybrid-watermark-system-agent-guide.md#18-document-authority-and-historical-notes) |

Current location warning: GCP is in `us-central1`; the live AWS deployment is in
`eu-north-1`. The Ohio migration document is a proposal and has not been executed. Never act on a
recorded resource state without refreshing it first, and never print broad Cloud Run or Lambda
environment dumps because they may contain secrets.

# AWS Guidance

- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the
  AWS CLI directly.
- Before starting a task, check whether a relevant AWS skill is available.
  Load the skill with `retrieve_skill` and prefer its guidance over
  general knowledge.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use
  hyphens instead.

## Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret,
  credential, API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST
  NOT hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with
  `asm-exec` so the secret resolves at runtime without entering context.


# Credentials to use when needing to login as a user and use the watermark system:
Email: moaaz@hotmail.com
Password: moaaz@hotmail.com
Store id: 2 (current active store)
