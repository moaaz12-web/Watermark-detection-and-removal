# AWS GPU A1 feasibility preflight

Date: 2026-07-24  
Region: `eu-north-1` (Europe/Stockholm)  
Mode: Read-only

This record captures the A1 feasibility checks for the AWS GPU worker. No GPU instance, AWS Batch
compute environment, repository, table, function, API, NAT gateway, budget, alert, or other resource
was created by these checks. Account identifiers and credentials are intentionally omitted.

## GPU capacity

- The `Running On-Demand G and VT instances` quota is `4` vCPUs.
- One `g4dn.xlarge` requires `4` vCPUs, so the quota permits at most one such instance at a time.
- `g4dn.xlarge` is offered in `eu-north-1a`, `eu-north-1b`, and `eu-north-1c`.
- This proves regional offering and quota feasibility. It does not guarantee that EC2 will have
  immediately available physical capacity when a future job is submitted.

Read-only calls:

```text
aws service-quotas get-service-quota
aws ec2 describe-instance-type-offerings
```

## Required regional services

The following service APIs all responded successfully in `eu-north-1`:

| Service | Read-only operation | Result |
| --- | --- | --- |
| AWS Batch | `DescribeJobQueues` | Available; no job queues currently exist. |
| Amazon ECR | `DescribeRepositories` | Available; no repositories currently exist. |
| Amazon VPC | `DescribeVpcs` | Available; the account has its default regional VPC. |
| CloudWatch Logs | `DescribeLogGroups` | Available; no log groups currently exist. |
| DynamoDB | `ListTables` | Available; no tables currently exist. |
| API Gateway HTTP API | `GetApis` | Available; no HTTP APIs currently exist. |
| AWS Lambda | `ListFunctions` | Available; no functions currently exist. |
| Amazon EventBridge | `ListEventBuses` | Available; only the AWS default event bus exists. |

An empty list is a successful availability result, not a missing service. No service resource was
created to perform these checks.

## Preliminary cost baseline

Prices below are USD, exclude taxes and credits, and were read from the AWS Price List API for
Stockholm on 2026-07-24. They must be refreshed before production rollout.

| Component | Stockholm price | Planning implication |
| --- | ---: | --- |
| On-Demand `g4dn.xlarge` Linux | `$0.558/hour` | Primary GPU runtime charge. |
| `gp3` EBS storage | `$0.0836/GB-month` | `200 GiB` is `$16.72/month` if retained continuously. When deleted with the instance, its approximate 90-minute cost is `$0.034`. |
| AWS Batch scheduler | No additional charge | Pay for EC2 and the supporting services used by the job. |
| Private ECR storage | `$0.10/GB-month` | A retained `10 GB` worker image is approximately `$1/month`; same-region image pulls to compute are free. |
| CloudWatch standard log ingestion | `$0.54/GB` | Usage-based, not a fixed monthly charge. |
| CloudWatch standard log storage | `$0.028/GB-month` | Retention must be bounded. |
| NAT gateway | `$0.046/hour` plus `$0.046/GB` processed | One continuously provisioned NAT gateway is about `$33.58/month` before data and internet-transfer charges. |

For the documented 90-minute acceptance timeout:

```text
GPU:               1.5 × $0.558                         = $0.837
Temporary 200 GiB: 200 × $0.0836 × (1.5 / 730)         = $0.034
Compute + storage subtotal                              = $0.871
```

The practical acceptance-job estimate is therefore **about `$0.87` plus logs and network
transfer** if the instance and EBS storage are released promptly. AWS bills actual runtime rather
than the timeout when a job finishes sooner.

Persistent baseline scenarios:

- Without a persistent NAT gateway: primarily retained ECR images and CloudWatch logs; roughly
  `$1.03/month` for a `10 GB` image and `1 GB` retained logs, before request-based usage.
- With one continuously available NAT gateway: roughly `$34.61/month` for that same example,
  before NAT data processing, internet transfer, request-based services, and taxes.
- A second NAT gateway for zonal redundancy would add another approximately `$33.58/month`.

API Gateway, Lambda, DynamoDB, and EventBridge are request-based and should be very small at the
initial job volume; their actual per-job cost will be measured after A3. Cross-cloud transfer cannot
be responsibly fixed yet because it depends on input/model/output byte counts. GCS-to-AWS traffic
can incur GCP egress, while AWS-to-GCS traffic can incur AWS internet egress and NAT processing.

The network design is therefore the main unresolved baseline-cost choice. No NAT gateway is created
by A1.

## Outcome

A1 feasibility is accepted:

- Stockholm has the required quota, instance offering, and regional service APIs.
- The minimum job cost is understood.
- The persistent NAT cost is explicitly visible for the later network decision.
- Budget creation was not part of the read-only preflight. After A1, global monthly budget
  `watermark-aws-monthly` was created and later raised to `$60`, with actual-spend alerts at `$5`, `$15`, and `$25`
  and a forecasted-spend alert at `$25`, all sent by email. It has no automated action, SNS topic,
  or paid report.

## Official sources

- [AWS Batch pricing](https://aws.amazon.com/batch/pricing/)
- [Amazon ECR pricing](https://aws.amazon.com/ecr/pricing/)
- [Amazon VPC and NAT gateway pricing](https://aws.amazon.com/vpc/pricing/)
- [Amazon CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)
- [AWS Price List `GetProducts` API](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html)
