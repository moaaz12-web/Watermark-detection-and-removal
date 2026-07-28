data "archive_file" "submission_lambda" {
  type        = "zip"
  source_dir  = local.lambda_source_dir
  output_path = local.lambda_zip_path
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = 30
}

resource "aws_lambda_function" "submission" {
  function_name = local.lambda_name
  description   = "Authenticated, idempotent AWS GPU submission boundary."
  role          = aws_iam_role.submission_lambda.arn
  handler       = "aws_submission.handler.lambda_handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]

  filename         = data.archive_file.submission_lambda.output_path
  source_code_hash = data.archive_file.submission_lambda.output_base64sha256

  memory_size                    = 512
  timeout                        = 30
  reserved_concurrent_executions = -1

  environment {
    variables = {
      APPROVED_MANIFEST_PREFIXES = jsonencode(var.approved_manifest_prefixes)
      APPROVED_OUTPUT_PREFIXES   = jsonencode(var.approved_output_prefixes)
      BATCH_JOB_DEFINITION_ARNS  = jsonencode(local.batch_job_definition_arns)
      BATCH_QUEUE_ARN            = aws_batch_job_queue.gpu.arn
      EXPECTED_GOOGLE_SUB        = google_service_account.orchestrator.unique_id
      LEDGER_TABLE_NAME          = aws_dynamodb_table.submission_ledger.name
      LEDGER_TTL_SECONDS         = tostring(var.ledger_ttl_seconds)
      PIPELINE_ALLOWLIST         = jsonencode(var.pipeline_allowlist)
      SUBMISSION_ENABLED         = tostring(var.submission_enabled)
    }
  }

  lifecycle {
    precondition {
      condition = (
        !var.submission_enabled ||
        (
          local.batch_image_ready &&
          var.batch_queue_enabled &&
          alltrue([
            for pipeline in var.pipeline_allowlist :
            try(local.batch_job_definition_arns[pipeline], "") != ""
          ])
        )
      )
      error_message = "Submission requires an A5 image digest, enabled queue, and exact job-definition revision."
    }
  }

  depends_on = [aws_iam_role_policy.submission_lambda]
}

resource "aws_apigatewayv2_api" "submission" {
  name          = local.api_name
  description   = "Internal GCP-to-AWS GPU submission boundary."
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_authorizer" "google" {
  api_id           = aws_apigatewayv2_api.submission.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${local.name_prefix}-google-jwt"

  jwt_configuration {
    audience = [var.google_jwt_audience]
    issuer   = var.google_jwt_issuer
  }
}

resource "aws_apigatewayv2_integration" "submission" {
  api_id                 = aws_apigatewayv2_api.submission.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.submission.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

locals {
  protected_routes = toset([
    "PUT /internal/v1/gpu-jobs/{applicationJobId}",
    "GET /internal/v1/gpu-jobs/{applicationJobId}",
    "PUT /internal/v1/gpu-jobs/{applicationJobId}/cancellation",
  ])
}

resource "aws_apigatewayv2_route" "protected" {
  for_each = local.protected_routes

  api_id             = aws_apigatewayv2_api.submission.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.submission.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.google.id
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.api_name}"
  retention_in_days = 30
}

resource "aws_apigatewayv2_stage" "staging" {
  api_id      = aws_apigatewayv2_api.submission.id
  name        = var.environment
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      apiId            = "$context.apiId"
      httpMethod       = "$context.httpMethod"
      integrationError = "$context.integrationErrorMessage"
      requestId        = "$context.requestId"
      routeKey         = "$context.routeKey"
      sourceIp         = "$context.identity.sourceIp"
      status           = "$context.status"
    })
  }

  default_route_settings {
    detailed_metrics_enabled = true
    throttling_burst_limit   = 10
    throttling_rate_limit    = 5
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.submission.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.submission.execution_arn}/*/*"
}
