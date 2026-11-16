# frontend

# Frontend files. Everything is sent with "no-cache" so a new `terraform apply` shows up at once
# (CloudFront revalidates with the ETag), no invalidation step needed.

resource "aws_s3_object" "frontend" {
  for_each = local.frontend_files

  bucket        = aws_s3_bucket.site.id
  key           = each.value
  source        = "${local.frontend_dir}/${each.value}"
  etag          = filemd5("${local.frontend_dir}/${each.value}")
  content_type  = lookup(local.mime_types, reverse(split(".", each.value))[0], "application/octet-stream")
  cache_control = "no-cache"
}

resource "aws_s3_object" "frontend_config" {
  bucket        = aws_s3_bucket.site.id
  key           = "config.js"
  content       = "window.CROPAGANDA_CONFIG = ${jsonencode(local.frontend_config)};\n"
  content_type  = "text/javascript; charset=utf-8"
  cache_control = "no-cache"
}
