resource "aws_s3_bucket" "codebuild_source" {
  bucket        = local.codebuild_source_bucket_name
  force_destroy = false
}

resource "aws_s3_bucket_ownership_controls" "codebuild_source" {
  bucket = aws_s3_bucket.codebuild_source.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "codebuild_source" {
  bucket = aws_s3_bucket.codebuild_source.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "codebuild_source" {
  bucket = aws_s3_bucket.codebuild_source.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "codebuild_source" {
  bucket = aws_s3_bucket.codebuild_source.id

  rule {
    id     = "expire-uploaded-build-sources"
    status = "Enabled"

    filter {
      prefix = "source/"
    }

    expiration {
      days = 7
    }
  }
}

resource "aws_ecr_repository" "worker_build_cache" {
  name                 = local.ecr_cache_repository_name
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = false
  }
}

resource "aws_ecr_lifecycle_policy" "worker_build_cache" {
  repository = aws_ecr_repository.worker_build_cache.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged build-cache manifests after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "codebuild_worker_image" {
  name              = local.codebuild_log_group
  retention_in_days = 14
}

data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codebuild_worker_image" {
  name               = "${local.codebuild_name}-role"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json
}

data "aws_iam_policy_document" "codebuild_worker_image" {
  statement {
    sid     = "ReadUploadedBuildSource"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.codebuild_source.arn}/source/*",
    ]
  }

  statement {
    sid     = "AuthenticateToEcr"
    actions = ["ecr:GetAuthorizationToken"]
    resources = [
      "*",
    ]
  }

  statement {
    sid = "PullAndPushWorkerAndCacheImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [
      aws_ecr_repository.worker.arn,
      aws_ecr_repository.worker_build_cache.arn,
    ]
  }

  statement {
    sid = "WriteBuildLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.codebuild_worker_image.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "codebuild_worker_image" {
  name   = local.codebuild_name
  role   = aws_iam_role.codebuild_worker_image.id
  policy = data.aws_iam_policy_document.codebuild_worker_image.json
}

resource "aws_codebuild_project" "worker_image" {
  name           = local.codebuild_name
  description    = "On-demand remote build for the staging GPU worker image"
  service_role   = aws_iam_role.codebuild_worker_image.arn
  build_timeout  = 120
  queued_timeout = 60
  badge_enabled  = false

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.codebuild_source.bucket}/source/operator-upload.zip"
    buildspec = "buildspec.yml"
  }

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_LARGE"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"
    privileged_mode             = true

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = var.aws_account_id
    }

    environment_variable {
      name  = "WORKER_REPOSITORY_URI"
      value = aws_ecr_repository.worker.repository_url
    }

    environment_variable {
      name  = "CACHE_REPOSITORY_URI"
      value = aws_ecr_repository.worker_build_cache.repository_url
    }

    environment_variable {
      name  = "GPU_BASE_IMAGE"
      value = var.gpu_base_image
    }

    environment_variable {
      name  = "IMAGE_TAG"
      value = "UNSET"
    }
  }

  logs_config {
    cloudwatch_logs {
      status     = "ENABLED"
      group_name = aws_cloudwatch_log_group.codebuild_worker_image.name
    }
  }

  cache {
    type = "NO_CACHE"
  }
}
