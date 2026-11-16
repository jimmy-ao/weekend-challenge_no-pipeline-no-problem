# cloudwatch

# lambda

resource "aws_cloudwatch_log_group" "api_fn" {
  name              = local.cloudwatch_log_group_api_fn_name
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "pipeline_fn" {
  for_each          = local.pipeline_functions
  name              = "/aws/lambda/${local.lambda_function_pipeline_names[each.key]}"
  retention_in_days = var.log_retention_days
}

# api gateway

resource "aws_cloudwatch_log_group" "api_access" {
  name              = local.cloudwatch_log_group_api_access_name
  retention_in_days = var.log_retention_days
}

# step functions

resource "aws_cloudwatch_log_group" "pipeline_sfn" {
  name              = local.cloudwatch_log_group_sfn_name
  retention_in_days = var.log_retention_days
}
