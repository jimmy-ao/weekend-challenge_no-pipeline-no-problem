# providers

# provider used to deploy: workload account, project region (everything except the items below)
provider "aws" {
  region  = var.region

  default_tags {
    tags = local.tags
  }
}

# provider used to create the certificate: workload account, us-east-1 (CloudFront only accepts ACM certificates from there)
provider "aws" {
  alias   = "use1"
  region  = "us-east-1"

  default_tags {
    tags = local.tags
  }
}

# provider used to handle route53 resources: the account holding the public hosted zone (archnops.com), DNS records only
provider "aws" {
  alias   = "r53"
  region  = "us-east-1"

  assume_role {
    role_arn = "arn:aws:iam::203918869615:role/p-r53-glb-allow-record-create"
  }

  default_tags {
    tags = local.tags
  }
}
