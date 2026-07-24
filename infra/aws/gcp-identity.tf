resource "google_service_account" "orchestrator" {
  account_id   = "watermark-aws-orchestrator"
  display_name = "Watermark AWS GPU submission orchestrator"
  description  = "Calls the authenticated AWS submission boundary from GCP."

  lifecycle {
    precondition {
      condition     = var.gcp_project_number != ""
      error_message = "The numeric GCP project number must be recorded."
    }
  }
}
