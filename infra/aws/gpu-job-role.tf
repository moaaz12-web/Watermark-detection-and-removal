data "aws_iam_policy_document" "gpu_job_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:*"]
    }
  }
}

resource "aws_iam_role" "gpu_job" {
  name               = "${local.name_prefix}-gpu-job-role"
  description        = "Task identity for the watermark AWS Batch GPU container."
  assume_role_policy = data.aws_iam_policy_document.gpu_job_assume.json

  tags = {
    Component = "gpu-worker-identity"
  }
}
