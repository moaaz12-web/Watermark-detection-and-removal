resource "aws_dynamodb_table" "submission_ledger" {
  name         = local.ledger_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "applicationJobId"

  attribute {
    name = "applicationJobId"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = true
}
