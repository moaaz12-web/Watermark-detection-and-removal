resource "google_project_service" "wif" {
  for_each = toset([
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])

  project            = var.gcp_project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "aws" {
  project                   = var.gcp_project_id
  workload_identity_pool_id = var.gcp_wif_pool_id
  display_name              = "Watermark AWS workloads"
  description               = "Federates the exact staging AWS GPU job role without key files."

  depends_on = [google_project_service.wif]
}

resource "google_iam_workload_identity_pool_provider" "aws_staging" {
  project                            = var.gcp_project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws.workload_identity_pool_id
  workload_identity_pool_provider_id = var.gcp_wif_provider_id
  display_name                       = "Watermark AWS staging GPU role"
  description                        = "Accepts only the staging GPU job role in the approved AWS account."

  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.account"  = "assertion.account"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role_name}/')"
  }

  attribute_condition = <<-EOT
    assertion.account == '${var.aws_account_id}' &&
    assertion.arn.startsWith('arn:aws:sts::${var.aws_account_id}:assumed-role/${aws_iam_role.gpu_job.name}/')
  EOT

  aws {
    account_id = var.aws_account_id
  }
}

resource "google_service_account_iam_member" "gpu_worker_wif" {
  service_account_id = "projects/${var.gcp_project_id}/serviceAccounts/${local.gpu_worker_service_account_email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = local.gpu_job_wif_principal_set
}
