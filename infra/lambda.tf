# lambda

# api function

resource "aws_lambda_function" "api" {
  function_name    = local.lambda_function_api_name
  role             = aws_iam_role.api.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "app.handler"
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  memory_size      = 512
  timeout          = 15

  environment {
    variables = merge(local.lambda_common_env, {
      RETENTION_DAYS = tostring(var.retention_days)
    })
  }

  depends_on = [aws_cloudwatch_log_group.api_fn, aws_iam_role_policy.api]
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

# pipeline functions (prepare / render / package): one package with Pillow + fonts

resource "aws_lambda_function" "pipeline" {
  for_each = local.pipeline_functions

  function_name    = local.lambda_function_pipeline_names[each.key]
  role             = aws_iam_role.pipeline.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = each.value.handler
  filename         = data.archive_file.pipeline.output_path
  source_code_hash = data.archive_file.pipeline.output_base64sha256
  memory_size      = each.value.memory
  timeout          = each.value.timeout

  ephemeral_storage {
    size = 512
  }

  environment {
    variables = local.lambda_common_env
  }

  depends_on = [aws_cloudwatch_log_group.pipeline_fn, aws_iam_role_policy.pipeline]
}
