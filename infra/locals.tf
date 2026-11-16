# locals

locals {
  env = substr(var.environment, 0, 1)
  reg = join("", [element((split("-", var.region)), 0), substr(element((split("-", var.region)), 1), 0, 1), element((split("-", var.region)), 2)])

  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  tags = {
    Environment = var.environment
    Location    = var.region
    ManagedBy   = "terraform"
    Project     = var.project
    Challenge   = "no-pipeline-no-problem"
  }

  site_origin = "https://${var.domain_name}"
  dev_origins = var.allow_localhost ? ["http://localhost:5173"] : []

  max_upload_bytes = var.max_upload_mb * 1024 * 1024
  build_dir        = "${path.module}/../build"

  #   s3
  s3_media_name = join("-", [local.env, "s3", local.reg, var.app, "media", local.account_id])
  s3_site_name  = join("-", [local.env, "s3", local.reg, var.app, "site", local.account_id])

  #   dynamodb
  dynamodb_table_events_name = join("-", [local.env, "ddb", local.reg, var.app, "table-events"])
  dynamodb_table_jobs_name   = join("-", [local.env, "ddb", local.reg, var.app, "table-jobs"])

  #   cognito
  cognito_user_pool_name        = join("-", [local.env, "cog", local.reg, var.app, "user-pool"])
  cognito_user_pool_client_name = join("-", [local.env, "cog", local.reg, var.app, "user-pool-client"])
  cognito_idp_url               = "https://cognito-idp.${var.region}.amazonaws.com"

  #   api gateway
  api_gateway_name            = join("-", [local.env, "agtw", local.reg, var.app, "api"])
  api_gateway_authorizer_name = join("-", [local.env, "agtw", local.reg, var.app, "authorizer-cognito"])

  api_routes = [
    "GET /formats",
    "GET /events",
    "POST /events",
    "PUT /events/{eventId}",
    "DELETE /events/{eventId}",
    "POST /events/{eventId}/logo",
    "GET /jobs",
    "POST /jobs",
    "GET /jobs/{jobId}",
    "DELETE /jobs/{jobId}",
  ]

  #   lambda
  lambda_function_api_name = join("-", [local.env, "lbd", local.reg, var.app, "api"])
  lambda_function_pipeline_names = {
    for fn in keys(local.pipeline_functions) : fn => join("-", [local.env, "lbd", local.reg, var.app, fn])
  }

  pipeline_functions = {
    prepare = { handler = "handlers.prepare", memory = 1024, timeout = 60 }
    render  = { handler = "handlers.render", memory = 2048, timeout = 90 }
    package = { handler = "handlers.package", memory = 1024, timeout = 60 }
  }

  lambda_common_env = {
    MEDIA_BUCKET     = aws_s3_bucket.media.id
    JOBS_TABLE       = aws_dynamodb_table.jobs.name
    EVENTS_TABLE     = aws_dynamodb_table.events.name
    MAX_UPLOAD_BYTES = tostring(local.max_upload_bytes)
  }

  #   iam (global)
  iam_role_api_name           = join("-", [local.env, "iam", "glb", var.app, "role-api"])
  iam_policy_api_name         = join("-", [local.env, "iam", "glb", var.app, "policy-api"])
  iam_role_pipeline_name      = join("-", [local.env, "iam", "glb", var.app, "role-pipeline"])
  iam_policy_pipeline_name    = join("-", [local.env, "iam", "glb", var.app, "policy-pipeline"])
  iam_role_sfn_name           = join("-", [local.env, "iam", "glb", var.app, "role-sfn"])
  iam_policy_sfn_name         = join("-", [local.env, "iam", "glb", var.app, "policy-sfn"])
  iam_role_eventbridge_name   = join("-", [local.env, "iam", "glb", var.app, "role-eventbridge"])
  iam_policy_eventbridge_name = join("-", [local.env, "iam", "glb", var.app, "policy-eventbridge"])

  #   step functions
  sfn_state_machine_name = join("-", [local.env, "sfn", local.reg, var.app, "pipeline"])

  #   eventbridge
  eventbridge_rule_upload_name = join("-", [local.env, "evb", local.reg, var.app, "photo-uploaded"])

  #   cloudwatch (log group names are fixed by each service: /aws/lambda/<function>, ...)
  cloudwatch_log_group_api_fn_name     = "/aws/lambda/${local.lambda_function_api_name}"
  cloudwatch_log_group_api_access_name = "/aws/apigateway/${local.api_gateway_name}"
  cloudwatch_log_group_sfn_name        = "/aws/vendedlogs/states/${local.sfn_state_machine_name}"

  #   cloudfront (global)
  cloudfront_oac_name            = join("-", [local.env, "cf", "glb", var.app, "oac-site"])
  cloudfront_headers_policy_name = join("-", [local.env, "cf", "glb", var.app, "security-headers"])

  # Built from names rather than resource attributes, so CloudFront does not depend on the
  # API / Cognito / bucket resources that themselves need the CloudFront URL (no dependency cycle).
  media_origin = "https://${local.s3_media_name}.s3.${var.region}.amazonaws.com"
  csp = join("; ", [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' blob: data: ${local.media_origin}",
    "connect-src 'self' https://*.execute-api.${var.region}.amazonaws.com ${local.cognito_idp_url} ${local.media_origin}",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "object-src 'none'",
  ])

  #   frontend
  frontend_dir   = "${path.module}/../frontend"
  frontend_files = setsubtract(fileset(local.frontend_dir, "**"), ["config.js"])
  mime_types = {
    html        = "text/html; charset=utf-8"
    css         = "text/css; charset=utf-8"
    js          = "text/javascript; charset=utf-8"
    svg         = "image/svg+xml"
    png         = "image/png"
    ico         = "image/x-icon"
    json        = "application/json"
    webmanifest = "application/manifest+json"
    woff2       = "font/woff2"
    txt         = "text/plain; charset=utf-8"
  }

  frontend_config = {
    region          = var.region
    apiUrl          = aws_apigatewayv2_api.http.api_endpoint
    cognitoClientId = aws_cognito_user_pool_client.web.id
  }
}
