resource "aws_cloudwatch_log_group" "batch_worker" {
  name              = "/aws/batch/${local.batch_job_name}"
  retention_in_days = 30

  tags = {
    Component = "gpu-worker-logs"
  }
}

locals {
  batch_worker_parameters = {
    JOB_ID              = "UNSET"
    MANIFEST_URI        = "UNSET"
    MANIFEST_GENERATION = "UNSET"
    OUTPUT_MANIFEST_URI = "UNSET"
    PIPELINE_VERSION    = "UNSET"
    PIPELINE_CONFIG     = "/app/configs/watermark-v1.yaml"
    PROVIDER_ATTEMPT    = "1"
  }

  batch_worker_container_properties = jsonencode({
    image = "${aws_ecr_repository.worker.repository_url}@${var.worker_image_digest}"
    command = [
      "--manifest-uri",
      "Ref::MANIFEST_URI",
      "--manifest-generation",
      "Ref::MANIFEST_GENERATION",
      "--config",
      "Ref::PIPELINE_CONFIG",
      "--model-manifest",
      "/app/model-manifest.json",
      "--worker-image-digest",
      var.worker_image_digest,
    ]
    environment = [
      {
        name  = "AWS_REGION"
        value = var.aws_region
      },
      {
        name  = "COMPUTE_PROVIDER"
        value = "AWS_BATCH"
      },
      {
        name  = "GCP_PROJECT_ID"
        value = var.gcp_project_id
      },
      {
        name  = "GCP_SERVICE_ACCOUNT_IMPERSONATION_URL"
        value = local.gcp_worker_impersonation_url
      },
      {
        name  = "GCP_WIF_AUDIENCE"
        value = local.gcp_wif_audience
      },
      {
        name  = "HF_HOME"
        value = "/tmp/watermark-worker/huggingface"
      },
      {
        name  = "TRANSFORMERS_CACHE"
        value = "/tmp/watermark-worker/huggingface/transformers"
      },
      {
        name  = "HUGGINGFACE_HUB_CACHE"
        value = "/tmp/watermark-worker/huggingface/hub"
      },
      {
        name  = "XDG_CACHE_HOME"
        value = "/tmp/watermark-worker/cache"
      },
    ]
    executionRoleArn       = aws_iam_role.batch_execution.arn
    jobRoleArn             = aws_iam_role.gpu_job.arn
    privileged             = false
    readonlyRootFilesystem = true
    user                   = "10001:10001"
    resourceRequirements = [
      {
        type  = "VCPU"
        value = "4"
      },
      {
        type  = "MEMORY"
        value = "14336"
      },
      {
        type  = "GPU"
        value = "1"
      },
    ]
    volumes = [
      {
        name = "worker-scratch"
      },
    ]
    mountPoints = [
      {
        sourceVolume  = "worker-scratch"
        containerPath = "/tmp/watermark-worker"
        readOnly      = false
      },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.batch_worker.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  })
}

resource "aws_batch_job_definition" "gpu_worker" {
  count = local.batch_image_ready ? 1 : 0

  name                  = local.batch_job_name
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  parameters           = local.batch_worker_parameters
  container_properties = local.batch_worker_container_properties

  retry_strategy {
    attempts = 1
  }

  timeout {
    attempt_duration_seconds = 5400
  }

  tags = {
    Component = "gpu-worker-definition"
  }

  depends_on = [
    aws_iam_role_policy_attachment.batch_execution,
  ]
}

resource "aws_batch_job_definition" "gpu_worker_bulk" {
  count = local.batch_image_ready ? 1 : 0

  name                  = "${local.batch_job_name}-bulk"
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  parameters = merge(local.batch_worker_parameters, {
    PIPELINE_CONFIG = "/app/configs/watermark-bulk-v1.yaml"
  })

  container_properties = local.batch_worker_container_properties

  retry_strategy {
    attempts = 2

    evaluate_on_exit {
      action           = "RETRY"
      on_status_reason = "Host EC2*"
    }

    evaluate_on_exit {
      action       = "RETRY"
      on_exit_code = "6"
    }

    evaluate_on_exit {
      action       = "RETRY"
      on_exit_code = "7"
    }

    evaluate_on_exit {
      action       = "EXIT"
      on_exit_code = "*"
    }
  }

  timeout {
    attempt_duration_seconds = 86400
  }

  tags = {
    Component = "gpu-worker-bulk-definition"
  }

  depends_on = [
    aws_iam_role_policy_attachment.batch_execution,
  ]
}

resource "aws_batch_job_definition" "gpu_worker_v2" {
  count = local.batch_image_ready ? 1 : 0

  name                  = "${local.batch_job_name}-v2"
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  parameters = merge(local.batch_worker_parameters, {
    PIPELINE_CONFIG = "/app/configs/watermark-v2.yaml"
  })

  container_properties = local.batch_worker_container_properties

  retry_strategy {
    attempts = 1
  }

  timeout {
    attempt_duration_seconds = 5400
  }

  tags = {
    Component = "gpu-worker-v2-definition"
  }

  depends_on = [
    aws_iam_role_policy_attachment.batch_execution,
  ]
}

resource "aws_batch_job_definition" "gpu_worker_bulk_v2" {
  count = local.batch_image_ready ? 1 : 0

  name                  = "${local.batch_job_name}-bulk-v2"
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  parameters = merge(local.batch_worker_parameters, {
    PIPELINE_CONFIG = "/app/configs/watermark-bulk-v2.yaml"
  })

  container_properties = local.batch_worker_container_properties

  retry_strategy {
    attempts = 2

    evaluate_on_exit {
      action           = "RETRY"
      on_status_reason = "Host EC2*"
    }

    evaluate_on_exit {
      action       = "RETRY"
      on_exit_code = "6"
    }

    evaluate_on_exit {
      action       = "RETRY"
      on_exit_code = "7"
    }

    evaluate_on_exit {
      action       = "EXIT"
      on_exit_code = "*"
    }
  }

  timeout {
    attempt_duration_seconds = 86400
  }

  tags = {
    Component = "gpu-worker-bulk-v2-definition"
  }

  depends_on = [
    aws_iam_role_policy_attachment.batch_execution,
  ]
}
