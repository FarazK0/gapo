# -------------------------------------------------------------------
# Scaling and blast radius controls.
#
# Read this before running a load test. Lambda will happily scale to the
# account concurrency limit, and every one of those containers is billable
# at 3 GB. An unbounded load test against an unbounded function is how a
# side project produces a four figure bill in an afternoon.
# -------------------------------------------------------------------

variable "reserved_concurrency" {
  type        = number
  default     = 50
  description = <<-EOT
    Hard ceiling on concurrent predict executions. Two jobs at once:

    1. Caps cost. 50 x 3 GB x 60 s worst case is a bounded number you can
       compute in advance, rather than an open question.
    2. Caps blast radius. A runaway client cannot consume the account's
       entire concurrency pool and starve the ingest function.

    Raise deliberately during a load test, then put it back. Set to -1 to
    remove the limit, which you should not do.
  EOT
}

variable "provisioned_concurrency" {
  type        = number
  default     = 0
  description = <<-EOT
    Pre-initialised containers, billed whether used or not. This is the
    only real fix for cold starts, and it is expensive at 3 GB: roughly
    38 USD per month per unit. Leave at 0 and rely on the warmer rule
    unless you have measured that cold starts actually matter to you.
  EOT
}

variable "monthly_budget_usd" {
  type        = number
  default     = 25
  description = "Alert threshold. Set this before your first load test, not after."
}

variable "budget_alert_email" {
  type        = string
  default     = ""
  description = "Leave empty to skip the budget. You should not leave it empty."
}

# Applied to the function defined in lambda.tf.
resource "aws_lambda_provisioned_concurrency_config" "predict" {
  count                             = var.provisioned_concurrency > 0 ? 1 : 0
  function_name                     = aws_lambda_function.predict.function_name
  qualifier                         = aws_lambda_alias.live.name
  provisioned_concurrent_executions = var.provisioned_concurrency
}

# Provisioned concurrency requires a version or alias, not $LATEST.
resource "aws_lambda_alias" "live" {
  name             = "live"
  function_name    = aws_lambda_function.predict.function_name
  function_version = aws_lambda_function.predict.version
}

# -------------------------------------------------------------------
# Cost guardrail
# -------------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  count        = var.budget_alert_email == "" ? 0 : 1
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}

# -------------------------------------------------------------------
# Load test visibility
# -------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "throttles" {
  alarm_name          = "${local.name}-predict-throttled"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.predict.function_name }
  alarm_description   = "Requests are being rejected. Either raise reserved_concurrency or the load is genuinely too high."
}

resource "aws_cloudwatch_dashboard" "loadtest" {
  dashboard_name = "${local.name}-loadtest"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", width = 12, height = 6, x = 0, y = 0
        properties = {
          title  = "Concurrency and throttles"
          region = var.region
          metrics = [
            ["AWS/Lambda", "ConcurrentExecutions", "FunctionName", aws_lambda_function.predict.function_name],
            [".", "Throttles", ".", "."],
          ]
          stat = "Maximum", period = 60
        }
      },
      {
        type = "metric", width = 12, height = 6, x = 12, y = 0
        properties = {
          title  = "Duration percentiles"
          region = var.region
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.predict.function_name, { stat = "p50" }],
            ["...", { stat = "p95" }],
            ["...", { stat = "p99" }],
            ["...", { stat = "Maximum" }],
          ]
          period = 60
        }
      },
      {
        type = "log", width = 24, height = 6, x = 0, y = 6
        properties = {
          title  = "Cold versus warm latency"
          region = var.region
          query = join(" | ", [
            "SOURCE '${aws_cloudwatch_log_group.predict.name}'",
            "filter event = 'predict'",
            "stats count() as calls, avg(ms) as avg_ms, pct(ms, 95) as p95_ms by cold",
          ])
        }
      },
    ]
  })
}
