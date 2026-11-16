# eventbridge

# "Object Created" on uploads/<jobId>/original starts the pipeline: no glue Lambda

resource "aws_cloudwatch_event_rule" "upload" {
  name        = local.eventbridge_rule_upload_name
  description = "Start the Cropaganda pipeline when a photo lands in uploads/"

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.media.id] }
      object = { key = [{ wildcard = "uploads/*/original" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "upload" {
  rule     = aws_cloudwatch_event_rule.upload.name
  arn      = aws_sfn_state_machine.pipeline.arn
  role_arn = aws_iam_role.events.arn

  input_transformer {
    input_paths = {
      bucket = "$.detail.bucket.name"
      key    = "$.detail.object.key"
    }
    input_template = <<-EOT
      {"bucket": <bucket>, "key": <key>}
    EOT
  }
}
