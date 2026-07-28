resource "aws_launch_template" "batch_gpu" {
  name_prefix            = "${local.name_prefix}-batch-gpu-"
  update_default_version = true

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      delete_on_termination = true
      encrypted             = true
      volume_size           = 200
      volume_type           = "gp3"
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_protocol_ipv6          = "disabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name      = "${local.name_prefix}-batch-gpu"
      Component = "gpu-batch-compute"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name      = "${local.name_prefix}-batch-gpu"
      Component = "gpu-batch-compute"
    }
  }

  tags = {
    Component = "gpu-batch-compute"
  }
}

resource "aws_batch_compute_environment" "gpu" {
  name         = local.batch_compute_name
  type         = "MANAGED"
  state        = var.batch_queue_enabled ? "ENABLED" : "DISABLED"
  service_role = aws_iam_service_linked_role.batch.arn

  compute_resources {
    type                = "EC2"
    allocation_strategy = "BEST_FIT"
    instance_type       = ["g4dn.xlarge"]
    min_vcpus           = 0
    desired_vcpus       = 0
    max_vcpus           = 4

    instance_role      = aws_iam_instance_profile.batch.arn
    security_group_ids = [aws_security_group.batch.id]
    subnets            = aws_subnet.batch_public[*].id

    ec2_configuration {
      image_type = "ECS_AL2023_NVIDIA"
    }

    launch_template {
      launch_template_id = aws_launch_template.batch_gpu.id
      version            = tostring(aws_launch_template.batch_gpu.latest_version)
    }

    tags = merge(local.common_tags, {
      Name      = "${local.name_prefix}-batch-gpu"
      Component = "gpu-batch-compute"
    })
  }

  tags = {
    Component = "gpu-batch-compute"
  }

  depends_on = [
    aws_iam_role_policy_attachment.batch_instance_ecs,
  ]
}

resource "aws_batch_job_queue" "gpu" {
  name     = local.batch_queue_name
  state    = var.batch_queue_enabled ? "ENABLED" : "DISABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.gpu.arn
  }

  tags = {
    Component = "gpu-batch-queue"
  }
}
