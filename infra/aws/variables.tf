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

variable "batch_queue_arn" {
  description = "Approved Batch queue ARN; intentionally empty during A3."
  type        = string
  default     = ""
}

variable "batch_job_definition_arn" {
  description = "Approved Batch job-definition ARN; intentionally empty during A3."
  type        = string
  default     = ""
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
