locals {
  name_prefix = "watermark-${var.environment}"

  lambda_name         = "${local.name_prefix}-submission"
  ledger_table_name   = "${local.name_prefix}-submission-ledger"
  api_name            = "${local.name_prefix}-gpu-submission"
  ecr_repository_name = "${local.name_prefix}-worker"
  batch_compute_name  = "${local.name_prefix}-gpu"
  batch_queue_name    = "${local.name_prefix}-gpu"
  batch_job_name      = "${local.name_prefix}-worker"

  lambda_source_dir = "${path.module}/../../services/aws_submission/build/lambda"
  lambda_zip_path   = "${path.module}/../../services/aws_submission/dist/submission-lambda.zip"

  gpu_worker_service_account_email = "watermark-gpu-worker@${var.gcp_project_id}.iam.gserviceaccount.com"
  gcp_wif_audience = join("", [
    "//iam.googleapis.com/projects/${var.gcp_project_number}/locations/global/",
    "workloadIdentityPools/${google_iam_workload_identity_pool.aws.workload_identity_pool_id}/",
    "providers/${google_iam_workload_identity_pool_provider.aws_staging.workload_identity_pool_provider_id}",
  ])
  gcp_worker_impersonation_url = join("", [
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/",
    "${local.gpu_worker_service_account_email}:generateAccessToken",
  ])
  gpu_job_wif_principal_set = join("", [
    "principalSet://iam.googleapis.com/projects/${var.gcp_project_number}/locations/global/",
    "workloadIdentityPools/${google_iam_workload_identity_pool.aws.workload_identity_pool_id}/",
    "attribute.aws_role/${aws_iam_role.gpu_job.name}",
  ])

  gcs_worker_read_prefixes = {
    for item in setunion(var.gcs_worker_read_prefixes, var.gcs_worker_output_prefixes) :
    "${item.bucket}/${item.prefix}" => item
  }
  gcs_worker_output_prefixes = {
    for item in var.gcs_worker_output_prefixes :
    "${item.bucket}/${item.prefix}" => item
  }

  batch_image_ready        = var.worker_image_digest != ""
  batch_job_definition_arn = try(aws_batch_job_definition.gpu_worker[0].arn, "")

  common_tags = {
    Application = "watermark"
    Component   = "gpu-submission-boundary"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
