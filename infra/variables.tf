# variables

variable "profile" {
  type        = string
  description = "Named AWS profile for the workload account (local runs). null = default credential chain (env vars, SSO, ...)."
  default     = null
}

variable "profile_r53" {
  type        = string
  description = "Named AWS profile for the account that owns the public hosted zone. Set it when the zone lives in another account."
  default     = null
}

variable "region" {
  type        = string
  description = "Region used to deploy resources in (everything except CloudFront, which is global)."
  default     = "eu-north-1"
}

variable "environment" {
  type        = string
  description = "Name of the environment these resources belong to."
  nullable    = false
  default     = "prd"

  validation {
    condition     = contains(["prd", "npe", "uat", "lab", "tst", "sbx"], var.environment)
    error_message = "The environment must be one of: prd, npe, uat, lab, tst, sbx."
  }
}

variable "project" {
  type        = string
  description = "Name of the project these resources belong to (Project tag)."
  default     = "cropaganda"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,30}$", var.project))
    error_message = "project must be 3-30 characters of lowercase letters, numbers or hyphens."
  }
}

variable "app" {
  type        = string
  description = "Short slug used to name resources. Lowercase letters, numbers and hyphens."
  default     = "cropaganda"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.app))
    error_message = "app must be 3-20 characters of lowercase letters, numbers or hyphens, starting with a letter."
  }
}

# route53 / acm

variable "domain_name" {
  type        = string
  description = "Public hostname of the app (CloudFront alias + ACM certificate)."
  default     = "cropaganda.archnops.com"
}

variable "hosted_zone_name" {
  type        = string
  description = "Public Route 53 hosted zone that domain_name belongs to."
  default     = "archnops.com"
}

# cognito

variable "producer_emails" {
  type        = list(string)
  description = "Content producers to invite. Cognito emails each one a temporary password. Self sign-up is disabled."
  default     = []
}

# s3 / dynamodb

variable "retention_days" {
  type        = number
  description = "Days before rendered photos and job records are deleted automatically."
  default     = 7
}

variable "max_upload_mb" {
  type        = number
  description = "Largest photo a producer can upload, in MB."
  default     = 25
}

variable "allow_localhost" {
  type        = bool
  description = "Allow http://localhost:5173 as a CORS origin (API and media bucket), for working on the frontend locally."
  default     = true
}

# cloudwatch

variable "log_retention_days" {
  type        = number
  description = "CloudWatch Logs retention for Lambda, API and Step Functions logs."
  default     = 14
}
