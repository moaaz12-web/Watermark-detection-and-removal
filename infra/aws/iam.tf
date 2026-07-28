data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "submission_lambda" {
  name               = "${local.name_prefix}-submission-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "submission_lambda" {
  statement {
    sid = "SubmissionLedger"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [aws_dynamodb_table.submission_ledger.arn]
  }

  statement {
    sid = "LambdaLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  dynamic "statement" {
    for_each = local.batch_image_ready ? [1] : []

    content {
      sid = "SubmitOnlyApprovedGpuJob"
      actions = [
        "batch:SubmitJob",
        "batch:TagResource",
      ]
      resources = concat(
        [aws_batch_job_queue.gpu.arn],
        values(local.batch_job_definition_arns),
        ["arn:aws:batch:${var.aws_region}:${var.aws_account_id}:job/*"],
      )

      condition {
        test     = "StringEquals"
        variable = "aws:RequestedRegion"
        values   = [var.aws_region]
      }
    }
  }

  dynamic "statement" {
    for_each = local.batch_image_ready ? [1] : []

    content {
      sid = "ListAndDescribeRegionJobs"
      actions = [
        "batch:DescribeJobs",
        "batch:ListJobs",
      ]
      resources = ["*"]

      condition {
        test     = "StringEquals"
        variable = "aws:RequestedRegion"
        values   = [var.aws_region]
      }
    }
  }

  dynamic "statement" {
    for_each = local.batch_image_ready ? [1] : []

    content {
      sid       = "TerminateAccountJobs"
      actions   = ["batch:TerminateJob"]
      resources = ["arn:aws:batch:${var.aws_region}:${var.aws_account_id}:job/*"]

      condition {
        test     = "StringEquals"
        variable = "aws:RequestedRegion"
        values   = [var.aws_region]
      }
    }
  }
}

resource "aws_iam_role_policy" "submission_lambda" {
  name   = "${local.name_prefix}-submission-lambda"
  role   = aws_iam_role.submission_lambda.id
  policy = data.aws_iam_policy_document.submission_lambda.json
}
