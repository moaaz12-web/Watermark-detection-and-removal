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
}

resource "aws_iam_role_policy" "submission_lambda" {
  name   = "${local.name_prefix}-submission-lambda"
  role   = aws_iam_role.submission_lambda.id
  policy = data.aws_iam_policy_document.submission_lambda.json
}
