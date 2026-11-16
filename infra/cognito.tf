# cognito

# Invite-only producers. The app has its own sign-in page that calls the Cognito API
# directly (InitiateAuth / RespondToAuthChallenge / ForgotPassword), no Hosted UI.

resource "aws_cognito_user_pool" "producers" {
  name                     = local.cognito_user_pool_name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = "INACTIVE"

  admin_create_user_config {
    allow_admin_create_user_only = true
    invite_message_template {
      email_subject = "Your Cropaganda invitation"
      email_message = "You have been invited to Cropaganda. Sign in with {username} and the temporary password {####} at ${local.site_origin}/"
      sms_message   = "Cropaganda: {username} / {####}"
    }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = local.cognito_user_pool_client_name
  user_pool_id = aws_cognito_user_pool.producers.id

  generate_secret = false
  # Password sign-in from the app's own page (over TLS to Cognito), plus refresh tokens.
  explicit_auth_flows           = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  auth_session_validity         = 5 # minutes to answer a challenge (e.g. set a new password)
  read_attributes               = ["email", "email_verified"]
  write_attributes              = []

  id_token_validity      = 60
  access_token_validity  = 60
  refresh_token_validity = 30
  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user" "producer" {
  for_each     = toset(var.producer_emails)
  user_pool_id = aws_cognito_user_pool.producers.id
  username     = each.value

  attributes = {
    email          = each.value
    email_verified = true
  }

  desired_delivery_mediums = ["EMAIL"]
}
