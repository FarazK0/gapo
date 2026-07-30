# -------------------------------------------------------------------
# HTTP API Gateway — sits between CloudFront and the predict Lambda.
#
# Lambda Function URLs with CloudFront OAC produce InvalidSignatureException
# because CloudFront normalises Accept-Encoding before signing, causing the
# body hash in the SigV4 signature to diverge. Routing through API Gateway
# avoids that signing step entirely while keeping the Lambda function URL
# locked to AWS_IAM so it cannot be called directly.
# -------------------------------------------------------------------
resource "aws_apigatewayv2_api" "predict" {
  name          = "${local.name}-predict"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "predict" {
  api_id                 = aws_apigatewayv2_api.predict.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.predict.invoke_arn
  payload_format_version = "2.0"
}

# Catch-all route so CloudFront can forward any /api/* path unchanged.
resource "aws_apigatewayv2_route" "predict" {
  api_id    = aws_apigatewayv2_api.predict.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.predict.id}"
}

resource "aws_apigatewayv2_stage" "predict" {
  api_id      = aws_apigatewayv2_api.predict.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.predict.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.predict.execution_arn}/*/*"
}
