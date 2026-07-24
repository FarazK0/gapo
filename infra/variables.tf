terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Bucket and table are created by bootstrap/bootstrap.yaml.
  # Values are injected by the workflow via -backend-config.
  backend "s3" {
    key     = "gapo/terraform.tfstate"
    encrypt = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

# CloudFront needs an ACM certificate in us-east-1 if you attach a custom
# domain. The alias is declared now so adding one later is not a refactor.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

variable "project" {
  type        = string
  default     = "gapo"
  description = "Name prefix for every resource"
}

variable "region" {
  type        = string
  default     = "eu-west-1"
  description = "Primary AWS region"
}

variable "image_tag" {
  type        = string
  description = "ECR image tag to deploy. The CI workflow passes the git SHA."
}

variable "universe" {
  type        = list(string)
  description = "Tickers the daily ingest job caches. Users may only select from this list."
  default = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "AVGO", "HD",
    "CVX", "MRK", "ABBV", "COST", "PEP", "KO", "ADBE", "CSCO", "CRM",
    "MCD", "TMO", "ACN", "BAC", "NFLX", "AMD", "LIN", "ABT", "DIS",
    "WFC", "TXN", "PM", "DHR", "VZ", "INTC", "CAT", "NEE", "AMGN",
    "IBM", "GE", "QCOM", "NOW", "UNP"
  ]
}

variable "lambda_memory_mb" {
  type        = number
  default     = 3008
  description = <<-EOT
    Lambda allocates CPU proportionally to memory. 3008 MB gives roughly
    2 vCPU, which cuts the torch import on cold start from ~12s to ~5s.
    Because billing is memory x duration, more memory here is close to
    cost neutral for a workload this short. Do not lower this without
    measuring.
  EOT
}

variable "keep_warm" {
  type        = bool
  default     = true
  description = "Ping the predict function every 5 minutes to avoid cold starts. Costs under 1 USD per month."
}

variable "log_retention_days" {
  type        = number
  default     = 14
  description = "CloudWatch retention. The default of never expiring is the classic surprise bill."
}
