terraform {
  # use_lockfile became generally available in 1.11.0.
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Backend blocks cannot use variables, so the bucket is passed at init time:
  # terraform init -backend-config="bucket=cropcms-tfstate-<account-id>"
  backend "s3" {
    key          = "cropcms/terraform.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = "eu-north-1"

  default_tags {
    tags = local.tags
  }
}

locals {
  tags = {
    Project   = "cropcms"
    ManagedBy = "terraform"
  }
}
