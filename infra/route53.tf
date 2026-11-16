# route53

# Records in the hosted zone, which lives in the Route 53 account

resource "aws_route53_record" "cert_validation" {
  provider = aws.r53
  for_each = {
    for o in aws_acm_certificate.site.domain_validation_options : o.domain_name => o
  }

  zone_id         = data.aws_route53_zone.public.zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_route53_record" "site" {
  provider = aws.r53
  for_each = toset(["A", "AAAA"]) # the distribution has IPv6 enabled

  zone_id = data.aws_route53_zone.public.zone_id
  name    = var.domain_name
  type    = each.value

  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}
