# iam

# api function

resource "aws_iam_role" "api" {
  name               = local.iam_role_api_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "api" {
  name   = local.iam_policy_api_name
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# pipeline functions (prepare / render / package)

resource "aws_iam_role" "pipeline" {
  name               = local.iam_role_pipeline_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "pipeline" {
  name   = local.iam_policy_pipeline_name
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline.json
}

# step functions state machine

resource "aws_iam_role" "sfn" {
  name               = local.iam_role_sfn_name
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

resource "aws_iam_role_policy" "sfn" {
  name   = local.iam_policy_sfn_name
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

# eventbridge rule (starts the pipeline)

resource "aws_iam_role" "events" {
  name               = local.iam_role_eventbridge_name
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

resource "aws_iam_role_policy" "events" {
  name   = local.iam_policy_eventbridge_name
  role   = aws_iam_role.events.id
  policy = data.aws_iam_policy_document.events.json
}
