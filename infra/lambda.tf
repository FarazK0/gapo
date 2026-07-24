# -------------------------------------------------------------------
# Execution roles. One per function, each scoped to what it touches.
# -------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "predict" {
  name               = "${local.name}-predict"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role" "ingest" {
  name               = "${local.name}-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "predict_logs" {
  role       = aws_iam_role.predict.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "ingest_logs" {
  role       = aws_iam_role.ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Predict reads cached features and writes history. It cannot write
# market data and it cannot reach the artifact bucket, because the
# model checkpoint is baked into the image at build time.
data "aws_iam_policy_document" "predict" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
  }
  statement {
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.history.arn]
  }
}

resource "aws_iam_role_policy" "predict" {
  name   = "access"
  role   = aws_iam_role.predict.id
  policy = data.aws_iam_policy_document.predict.json
}

data "aws_iam_policy_document" "ingest" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "access"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

# -------------------------------------------------------------------
# Log groups declared explicitly so retention is enforced. If Lambda
# creates them implicitly they retain forever.
# -------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "predict" {
  name              = "/aws/lambda/${local.name}-predict"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.name}-ingest"
  retention_in_days = var.log_retention_days
}

# -------------------------------------------------------------------
# Predict function
#
# Container image because PyTorch does not fit in a 250 MB zip layer.
# The image digest pins both the code and the model checkpoint, so a
# rollback restores the exact model that was serving before.
# -------------------------------------------------------------------
resource "aws_lambda_function" "predict" {
  function_name = "${local.name}-predict"
  role          = aws_iam_role.predict.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  memory_size   = var.lambda_memory_mb
  timeout       = 60
  architectures = ["x86_64"]
  publish       = true  # required for the alias used by provisioned concurrency

  reserved_concurrent_executions = var.reserved_concurrency

  image_config {
    command = ["handler.predict"]
  }

  environment {
    variables = {
      DATA_BUCKET   = aws_s3_bucket.data.id
      HISTORY_TABLE = aws_dynamodb_table.history.name
      MODEL_PATH    = "/opt/model/full_model.pth"
      UNIVERSE      = join(",", var.universe)
      LOG_LEVEL     = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.predict]
}

resource "aws_lambda_function" "ingest" {
  function_name = "${local.name}-ingest"
  role          = aws_iam_role.ingest.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  memory_size   = 1024
  timeout       = 600
  architectures = ["x86_64"]

  image_config {
    command = ["handler.ingest"]
  }

  environment {
    variables = {
      DATA_BUCKET = aws_s3_bucket.data.id
      UNIVERSE    = join(",", var.universe)
      LOG_LEVEL   = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest]
}

# -------------------------------------------------------------------
# Function URL, fronted by CloudFront.
#
# AWS_IAM auth plus origin access control means the function URL cannot
# be called directly. Only signed requests from your distribution work.
# This is what replaces an ALB, and it saves about 18 USD a month.
# -------------------------------------------------------------------
resource "aws_lambda_function_url" "predict" {
  function_name      = aws_lambda_function.predict.function_name
  authorization_type = "AWS_IAM"
  invoke_mode        = "BUFFERED"
}

resource "aws_lambda_permission" "cloudfront_invoke" {
  statement_id           = "AllowCloudFrontInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.predict.function_name
  principal              = "cloudfront.amazonaws.com"
  source_arn             = aws_cloudfront_distribution.web.arn
  function_url_auth_type = "AWS_IAM"
}

# -------------------------------------------------------------------
# Schedules
# -------------------------------------------------------------------

# 22:30 UTC on weekdays, comfortably after the US close at 21:00 UTC.
resource "aws_cloudwatch_event_rule" "daily_ingest" {
  name                = "${local.name}-daily-ingest"
  schedule_expression = "cron(30 22 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_target" "daily_ingest" {
  rule = aws_cloudwatch_event_rule.daily_ingest.name
  arn  = aws_lambda_function.ingest.arn
}

resource "aws_lambda_permission" "daily_ingest" {
  statement_id  = "AllowEventBridgeIngest"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_ingest.arn
}

resource "aws_cloudwatch_event_rule" "warmer" {
  count               = var.keep_warm ? 1 : 0
  name                = "${local.name}-warmer"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "warmer" {
  count = var.keep_warm ? 1 : 0
  rule  = aws_cloudwatch_event_rule.warmer[0].name
  arn   = aws_lambda_function.predict.arn
  input = jsonencode({ warmup = true })
}

resource "aws_lambda_permission" "warmer" {
  count         = var.keep_warm ? 1 : 0
  statement_id  = "AllowEventBridgeWarmer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.predict.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.warmer[0].arn
}

# -------------------------------------------------------------------
# Alarms. A pipeline that fails silently is worse than no pipeline.
# -------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "ingest_failed" {
  alarm_name          = "${local.name}-ingest-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 86400
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.ingest.function_name }
}

resource "aws_cloudwatch_metric_alarm" "predict_errors" {
  alarm_name          = "${local.name}-predict-error-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.predict.function_name }
}
