variable "environment" {
  description = "Deployment environment."
  type        = string
  default     = "staging"

  validation {
    condition     = var.environment == "staging"
    error_message = "A3 is restricted to the staging environment."
  }
}

variable "aws_region" {
  description = "AWS deployment region."
  type        = string
  default     = "eu-north-1"

  validation {
    condition     = var.aws_region == "eu-north-1"
    error_message = "The accepted A0 region is eu-north-1."
  }
}

variable "aws_account_id" {
  description = "Expected AWS account ID; the provider rejects any other account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "gcp_project_id" {
  description = "GCP project containing the core application."
  type        = string
}

variable "gcp_project_number" {
  description = "Numeric GCP project number."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.gcp_project_number))
    error_message = "gcp_project_number must be numeric."
  }
}

variable "gcp_wif_pool_id" {
  description = "Dedicated workload identity pool for AWS watermark workloads."
  type        = string
  default     = "watermark-aws"

  validation {
    condition     = var.gcp_wif_pool_id == "watermark-aws"
    error_message = "The accepted staging WIF pool ID is watermark-aws."
  }
}

variable "gcp_wif_provider_id" {
  description = "AWS provider within the watermark workload identity pool."
  type        = string
  default     = "staging"

  validation {
    condition     = var.gcp_wif_provider_id == "staging"
    error_message = "The accepted AWS WIF provider ID is staging."
  }
}

variable "gcs_worker_read_prefixes" {
  description = "Bucket/object prefixes the federated GPU worker may read."
  type = set(object({
    bucket = string
    prefix = string
  }))

  validation {
    condition = (
      length(var.gcs_worker_read_prefixes) > 0 &&
      alltrue([
        for item in var.gcs_worker_read_prefixes :
        can(regex("^[a-z0-9][a-z0-9._-]{1,221}[a-z0-9]$", item.bucket)) &&
        can(regex("^[A-Za-z0-9][A-Za-z0-9._/-]*/$", item.prefix)) &&
        !strcontains(item.prefix, "..")
      ])
    )
    error_message = "Worker read prefixes must use valid bucket names and safe object prefixes ending in /."
  }
}

variable "gcs_worker_output_prefixes" {
  description = "Bucket/object prefixes where the federated GPU worker may create outputs."
  type = set(object({
    bucket = string
    prefix = string
  }))

  validation {
    condition = (
      length(var.gcs_worker_output_prefixes) > 0 &&
      alltrue([
        for item in var.gcs_worker_output_prefixes :
        can(regex("^[a-z0-9][a-z0-9._-]{1,221}[a-z0-9]$", item.bucket)) &&
        can(regex("^[A-Za-z0-9][A-Za-z0-9._/-]*/$", item.prefix)) &&
        !strcontains(item.prefix, "..")
      ])
    )
    error_message = "Worker output prefixes must use valid bucket names and safe object prefixes ending in /."
  }
}

variable "google_jwt_issuer" {
  description = "Issuer for Google-signed service-account ID tokens."
  type        = string
  default     = "https://accounts.google.com"

  validation {
    condition     = var.google_jwt_issuer == "https://accounts.google.com"
    error_message = "The Google service-account ID-token issuer must remain fixed."
  }
}

variable "google_jwt_audience" {
  description = "Dedicated audience for the AWS submission boundary."
  type        = string
  default     = "urn:watermark:aws-gpu-submission:staging"

  validation {
    condition     = var.google_jwt_audience == "urn:watermark:aws-gpu-submission:staging"
    error_message = "The staging audience must remain dedicated and exact."
  }
}

variable "approved_manifest_prefixes" {
  description = "GCS manifest URI prefixes accepted from the orchestrator."
  type        = list(string)

  validation {
    condition = (
      length(var.approved_manifest_prefixes) > 0 &&
      alltrue([
        for value in var.approved_manifest_prefixes :
        startswith(value, "gs://") && endswith(value, "/")
      ])
    )
    error_message = "Manifest prefixes must be non-empty gs:// prefixes ending in /."
  }
}

variable "approved_output_prefixes" {
  description = "GCS output URI prefixes accepted from the orchestrator."
  type        = list(string)

  validation {
    condition = (
      length(var.approved_output_prefixes) > 0 &&
      alltrue([
        for value in var.approved_output_prefixes :
        startswith(value, "gs://") && endswith(value, "/")
      ])
    )
    error_message = "Output prefixes must be non-empty gs:// prefixes ending in /."
  }
}

variable "pipeline_allowlist" {
  description = "Server-approved worker pipelines."
  type        = list(string)
  default     = ["watermark-v1"]

  validation {
    condition = (
      length(var.pipeline_allowlist) == 1 &&
      var.pipeline_allowlist[0] == "watermark-v1"
    )
    error_message = "The A2 contract allows only watermark-v1."
  }
}

variable "submission_enabled" {
  description = "Whether Lambda may reserve and submit a Batch job."
  type        = bool
  default     = false
}

variable "batch_queue_enabled" {
  description = "Whether the dedicated GPU job queue accepts submissions."
  type        = bool
  default     = false
}

variable "worker_image_digest" {
  description = "A5 ECR image digest (sha256:...) used to create the immutable Batch job definition; empty holds at the A5 gate."
  type        = string
  default     = ""

  validation {
    condition = (
      var.worker_image_digest == "" ||
      can(regex("^sha256:[0-9a-f]{64}$", var.worker_image_digest))
    )
    error_message = "worker_image_digest must be empty or an immutable sha256:<64 lowercase hex> digest."
  }
}

variable "gpu_base_image" {
  description = "Reviewed Linux AMD64 CUDA/PyTorch base image pinned by digest for remote worker builds."
  type        = string
  default     = "docker.io/pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime@sha256:831247999fbf7e08f61b3e39f6d77ee434f38f6f07f769d00db451e853878067"

  validation {
    condition = can(regex(
      "^docker\\.io/pytorch/pytorch:[a-zA-Z0-9._-]+@sha256:[0-9a-f]{64}$",
      var.gpu_base_image,
    ))
    error_message = "gpu_base_image must be the reviewed Docker Hub PyTorch image pinned by sha256 digest."
  }
}

variable "ledger_ttl_seconds" {
  description = "Submission-ledger retention period."
  type        = number
  default     = 7776000

  validation {
    condition     = var.ledger_ttl_seconds == 7776000
    error_message = "The accepted staging ledger TTL is 90 days."
  }
}
