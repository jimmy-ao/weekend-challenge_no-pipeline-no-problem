# data

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# route53

data "aws_route53_zone" "public" {
  provider     = aws.r53
  name         = var.hosted_zone_name
  private_zone = false
}

# lambda

# Packages are built by `make build` (scripts/build.sh) into ../build before terraform runs.
data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${local.build_dir}/api"
  output_path = "${local.build_dir}/api.zip"
}

data "archive_file" "pipeline" {
  type        = "zip"
  source_dir  = "${local.build_dir}/pipeline"
  output_path = "${local.build_dir}/pipeline.zip"
}

# iam: trust policies

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

# iam: permissions

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api_fn.arn}:*"]
  }
  statement {
    sid = "Tables"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem",
      "dynamodb:Query", "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.events.arn,
      aws_dynamodb_table.jobs.arn,
      "${aws_dynamodb_table.jobs.arn}/index/byOwner",
    ]
  }
  # Presigned URLs carry the permissions of the role that signs them.
  statement {
    sid       = "SignUploads"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.media.arn}/uploads/*", "${aws_s3_bucket.media.arn}/logos/*"]
  }
  statement {
    sid       = "SignDownloads"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/outputs/*", "${aws_s3_bucket.media.arn}/logos/*"]
  }
  statement {
    sid       = "Cleanup"
    actions   = ["s3:DeleteObject"]
    resources = ["${aws_s3_bucket.media.arn}/*"]
  }
  statement {
    sid       = "ListForCleanup"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for lg in aws_cloudwatch_log_group.pipeline_fn : "${lg.arn}:*"]
  }
  statement {
    sid     = "ReadMedia"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.media.arn}/uploads/*",
      "${aws_s3_bucket.media.arn}/logos/*",
      "${aws_s3_bucket.media.arn}/outputs/*",
    ]
  }
  statement {
    sid       = "WriteOutputs"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.media.arn}/outputs/*"]
  }
  statement {
    # Lets HeadObject return 404 rather than 403 when an object is missing.
    sid       = "List"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }
  statement {
    sid       = "Jobs"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.jobs.arn]
  }
  statement {
    sid       = "Events"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.events.arn]
  }
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid     = "InvokeTasks"
    actions = ["lambda:InvokeFunction"]
    resources = flatten([
      for fn in aws_lambda_function.pipeline : [fn.arn, "${fn.arn}:*"]
    ])
  }
  statement {
    sid       = "MarkFailed"
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.jobs.arn]
  }
  statement {
    # Vended log delivery needs these on "*" (see the Step Functions logging docs).
    sid = "Logging"
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "events" {
  statement {
    sid       = "StartPipeline"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }
}
