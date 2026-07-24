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
