# step functions

# Prepare -> Map(Render per format) -> Package, failures marked on the job

resource "aws_sfn_state_machine" "pipeline" {
  name     = local.sfn_state_machine_name
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/pipeline.asl.json", {
    prepare_arn = aws_lambda_function.pipeline["prepare"].arn
    render_arn  = aws_lambda_function.pipeline["render"].arn
    package_arn = aws_lambda_function.pipeline["package"].arn
    jobs_table  = aws_dynamodb_table.jobs.name
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.pipeline_sfn.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.sfn]
}
