resource "aws_cloudwatch_log_group" "batch_worker" {
  name              = "/aws/batch/${local.batch_job_name}"
  retention_in_days = 30

  tags = {
    Component = "gpu-worker-logs"
  }
}

resource "aws_batch_job_definition" "gpu_worker" {
  count = local.batch_image_ready ? 1 : 0

  name                  = local.batch_job_name
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  parameters = {
    JOB_ID              = "UNSET"
    MANIFEST_URI        = "UNSET"
    MANIFEST_GENERATION = "UNSET"
    OUTPUT_MANIFEST_URI = "UNSET"
    PIPELINE_VERSION    = "UNSET"
    PROVIDER_ATTEMPT    = "1"
  }

  container_properties = jsonencode({
    image = "${aws_ecr_repository.worker.repository_url}@${var.worker_image_digest}"
    command = [
      "--manifest-uri",
      "Ref::MANIFEST_URI",
      "--manifest-generation",
      "Ref::MANIFEST_GENERATION",
      "--config",
      "/app/configs/watermark-v1.yaml",
      "--model-manifest",
      "/app/model-manifest.json",
      "--worker-image-digest",
      var.worker_image_digest,
    ]
    environment = [
      {
        name  = "COMPUTE_PROVIDER"
        value = "AWS_BATCH"
      },
      {
        name  = "AWS_REGION"
        value = var.aws_region
      },
      {
        name  = "GCP_PROJECT_ID"
        value = var.gcp_project_id
      },
      {
        name  = "GCP_WIF_AUDIENCE"
        value = local.gcp_wif_audience
      },
      {
        name  = "GCP_SERVICE_ACCOUNT_IMPERSONATION_URL"
        value = local.gcp_worker_impersonation_url
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
        host = {}
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
