# outputs

output "site_url" {
  description = "Open this to use Cropaganda."
  value       = "${local.site_origin}/"
}

output "cloudfront_domain" {
  description = "Default CloudFront hostname (still answers, but CORS and Cognito links use site_url)."
  value       = aws_cloudfront_distribution.site.domain_name
}

output "api_url" {
  description = "HTTP API endpoint."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "user_pool_id" {
  description = "Invite more producers: aws cognito-idp admin-create-user --user-pool-id <this> --username someone@example.com"
  value       = aws_cognito_user_pool.producers.id
}

output "state_machine_arn" {
  description = "Step Functions pipeline."
  value       = aws_sfn_state_machine.pipeline.arn
}

output "media_bucket" {
  description = "Bucket holding uploads, logos and rendered photos."
  value       = aws_s3_bucket.media.id
}

output "frontend_config_js" {
  description = "Write to frontend/config.js to run the frontend on localhost:5173 (make dev)."
  value       = "window.CROPAGANDA_CONFIG = ${jsonencode(local.frontend_config)};\n"
}
