output "site_url" {
  description = "Open this in a browser once the deploy finishes"
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "web_bucket" {
  value = aws_s3_bucket.web.id
}

output "data_bucket" {
  value = aws_s3_bucket.data.id
}

output "distribution_id" {
  value = aws_cloudfront_distribution.web.id
}

output "predict_function" {
  value = aws_lambda_function.predict.function_name
}

output "ingest_function" {
  value = aws_lambda_function.ingest.function_name
}

output "api_gateway_url" {
  description = "API Gateway invoke URL (fronted by CloudFront at /api/*)"
  value       = aws_apigatewayv2_stage.predict.invoke_url
}

output "user_portfolios_table" {
  value = aws_dynamodb_table.user_portfolios.name
}

output "portfolio_history_table" {
  value = aws_dynamodb_table.portfolio_history.name
}
