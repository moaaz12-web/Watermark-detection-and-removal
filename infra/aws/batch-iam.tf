resource "aws_iam_service_linked_role" "batch" {
  aws_service_name = "batch.amazonaws.com"
  description      = "Service-linked role for the bounded watermark Batch environment."
}

data "aws_iam_policy_document" "batch_instance_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "batch_instance" {
  name               = "${local.name_prefix}-batch-instance-role"
  description        = "ECS host identity for ephemeral Batch GPU instances."
  assume_role_policy = data.aws_iam_policy_document.batch_instance_assume.json

  tags = {
    Component = "gpu-batch-compute"
  }
}

resource "aws_iam_role_policy_attachment" "batch_instance_ecs" {
  role       = aws_iam_role.batch_instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "batch" {
  name = "${local.name_prefix}-batch-instance-profile"
  role = aws_iam_role.batch_instance.name
}

data "aws_iam_policy_document" "batch_execution_assume" {
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

resource "aws_iam_role" "batch_execution" {
  name               = "${local.name_prefix}-batch-execution-role"
  description        = "ECR pull and awslogs identity for the Batch GPU task."
  assume_role_policy = data.aws_iam_policy_document.batch_execution_assume.json

  tags = {
    Component = "gpu-batch-task-execution"
  }
}

resource "aws_iam_role_policy_attachment" "batch_execution" {
  role       = aws_iam_role.batch_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
