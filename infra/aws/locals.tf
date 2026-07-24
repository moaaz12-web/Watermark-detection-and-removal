locals {
  name_prefix = "watermark-${var.environment}"

  lambda_name       = "${local.name_prefix}-submission"
  ledger_table_name = "${local.name_prefix}-submission-ledger"
  api_name          = "${local.name_prefix}-gpu-submission"

  lambda_source_dir = "${path.module}/../../services/aws_submission/build/lambda"
  lambda_zip_path   = "${path.module}/../../services/aws_submission/dist/submission-lambda.zip"

  common_tags = {
    Application = "watermark"
    Component   = "gpu-submission-boundary"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
