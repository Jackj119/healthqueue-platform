terraform {
  required_version = ">= 1.8.0"

  # State lives in the bucket created by infra/bootstrap. Backend blocks cannot
  # use variables, so the LocalStack endpoint is fixed here; override it with
  # -backend-config if LocalStack runs elsewhere.
  backend "s3" {
    bucket = "healthqueue-tofu-state"
    key    = "infra/terraform.tfstate"
    region = "us-east-1"

    endpoints = {
      s3 = "http://localhost:4566"
    }
    use_path_style = true

    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_requesting_account_id  = true

    # Native S3 locking (a .tflock object next to the state); no DynamoDB table.
    use_lockfile = true
  }

  # State and plan files are encrypted client-side before they reach the backend.
  # enforced = true makes OpenTofu refuse to write either one unencrypted.
  encryption {
    key_provider "pbkdf2" "state" {
      passphrase = var.state_passphrase
    }

    method "aes_gcm" "state" {
      keys = key_provider.pbkdf2.state
    }

    state {
      method   = method.aes_gcm.state
      enforced = true
    }

    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# This project targets LocalStack only; every service the config uses is
# routed to it. Credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
# (dummy values such as "test" are fine). The skip_* flags stop the provider
# from contacting real AWS to validate them.
provider "aws" {
  region = var.region

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  # LocalStack serves S3 at localhost:4566, not <bucket>.s3.amazonaws.com.
  s3_use_path_style = true

  endpoints {
    ec2 = var.localstack_endpoint
    s3  = var.localstack_endpoint
    sts = var.localstack_endpoint
    iam = var.localstack_endpoint
  }

  default_tags {
    tags = {
      Environment = var.environment
      ManagedBy   = "opentofu"
    }
  }
}

locals {
  name_prefix = "healthqueue-${var.environment}"
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${local.name_prefix}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-igw"
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = {
    Name = "${local.name_prefix}-public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${local.name_prefix}-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# No inbound rules by default; add ingress (e.g. SSH from your IP) as needed.
resource "aws_security_group" "instance" {
  name        = "${local.name_prefix}-instance-sg"
  description = "Security group for ${var.environment} instances"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name_prefix}-instance-sg"
  }
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

# S3 bucket names are globally unique, so append a random suffix.
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "main" {
  bucket = "${local.name_prefix}-${random_id.bucket_suffix.hex}"

  tags = {
    Name = "${local.name_prefix}-bucket"
  }
}

resource "aws_s3_bucket_public_access_block" "main" {
  bucket = aws_s3_bucket.main.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
