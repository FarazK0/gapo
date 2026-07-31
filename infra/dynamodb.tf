resource "aws_dynamodb_table" "user_portfolios" {
  name         = "${local.name}-user-portfolios"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "portfolio_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "portfolio_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}

# portfolio_pk stores the composite value "user_id#portfolio_id".
resource "aws_dynamodb_table" "portfolio_history" {
  name         = "${local.name}-portfolio-run-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "portfolio_pk"
  range_key    = "as_of"

  attribute {
    name = "portfolio_pk"
    type = "S"
  }

  attribute {
    name = "as_of"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}
