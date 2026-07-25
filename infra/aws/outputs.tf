output "api_base_url" {
  description = "Base URL for the internal staging submission API."
  value       = aws_apigatewayv2_stage.staging.invoke_url
}

output "api_id" {
  description = "HTTP API identifier."
  value       = aws_apigatewayv2_api.submission.id
}

output "lambda_function_name" {
  description = "Submission Lambda function name."
  value       = aws_lambda_function.submission.function_name
}

output "ledger_table_name" {
  description = "DynamoDB submission-ledger table name."
  value       = aws_dynamodb_table.submission_ledger.name
}

output "ecr_worker_repository_arn" {
  description = "ARN of the private immutable worker image repository."
  value       = aws_ecr_repository.worker.arn
}

output "ecr_worker_repository_url" {
  description = "Registry URL used to tag and push the reviewed worker image."
  value       = aws_ecr_repository.worker.repository_url
}

output "orchestrator_service_account_email" {
  description = "Dedicated GCP orchestrator service-account email."
  value       = google_service_account.orchestrator.email
}

output "orchestrator_service_account_unique_id" {
  description = "Numeric service-account uniqueId required by Lambda."
  value       = google_service_account.orchestrator.unique_id
}

output "submission_enabled" {
  description = "Safety state of real Batch submission."
  value       = var.submission_enabled
}

output "verified_aws_account_id" {
  description = "AWS account accepted by the provider account guard."
  value       = data.aws_caller_identity.current.account_id
}

output "gpu_job_role_arn" {
  description = "Task role to attach to the future AWS Batch job definition."
  value       = aws_iam_role.gpu_job.arn
}

output "gcp_wif_provider_name" {
  description = "Canonical Google AWS workload identity provider resource name."
  value       = google_iam_workload_identity_pool_provider.aws_staging.name
}

output "gcp_wif_audience" {
  description = "Audience configured in the AWS worker credential adapter."
  value       = local.gcp_wif_audience
}

output "gcp_worker_impersonation_url" {
  description = "Non-secret IAM Credentials endpoint used by the AWS worker."
  value       = local.gcp_worker_impersonation_url
}

output "batch_compute_environment_arn" {
  description = "Bounded managed EC2 compute environment; min and desired capacity are zero."
  value       = aws_batch_compute_environment.gpu.arn
}

output "batch_job_queue_arn" {
  description = "Dedicated GPU queue; disabled unless batch_queue_enabled is explicitly true."
  value       = aws_batch_job_queue.gpu.arn
}

output "batch_job_definition_arn" {
  description = "Exact immutable worker job-definition revision, or null while the A5 digest gate is open."
  value       = local.batch_image_ready ? local.batch_job_definition_arn : null
}

output "batch_image_ready" {
  description = "Whether a valid A5 digest was supplied to create the job definition and Lambda Batch permissions."
  value       = local.batch_image_ready
}
