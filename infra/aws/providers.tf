provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = local.common_tags
  }
}

provider "google" {
  project = var.gcp_project_id
}

data "aws_caller_identity" "current" {}
