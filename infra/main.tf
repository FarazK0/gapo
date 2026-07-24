data "aws_caller_identity" "current" {}

locals {
  name            = var.project
  account_id      = data.aws_caller_identity.current.account_id
  artifact_bucket = "${var.project}-artifacts-${data.aws_caller_identity.current.account_id}"
}

# -------------------------------------------------------------------
# Container registry
#
# The predict image carries PyTorch and the model checkpoint, so it is
# roughly 1.5 GB. The lifecycle policy is not optional at that size.
# -------------------------------------------------------------------
resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# -------------------------------------------------------------------
# Market data cache
#
# The daily ingest job writes one parquet object per ticker plus a
# pointer to the last known good snapshot. Inference never calls Yahoo.
# -------------------------------------------------------------------
resource "aws_s3_bucket" "data" {
  bucket = "${local.name}-marketdata-${local.account_id}"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-old-snapshots"
    status = "Enabled"
    filter {
      prefix = "snapshots/"
    }
    expiration {
      days = 90
    }
  }
}

# -------------------------------------------------------------------
# Portfolio history
#
# This replaces portfolio_data.json. On-demand billing means it costs
# effectively nothing at low volume and needs no capacity planning.
# The TTL attribute lets anonymous sessions expire on their own.
# -------------------------------------------------------------------
resource "aws_dynamodb_table" "history" {
  name         = "${local.name}-portfolio-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "created_at"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}
