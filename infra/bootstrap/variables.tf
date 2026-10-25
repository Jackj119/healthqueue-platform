variable "region" {
  description = "AWS region to create the state bucket in."
  type        = string
  default     = "us-east-1"
}

variable "localstack_endpoint" {
  description = "LocalStack edge endpoint that all AWS service calls are sent to."
  type        = string
  default     = "http://localhost:4566"
}

variable "state_bucket_name" {
  description = "Name of the bucket that stores infra/ state. Must match the backend block in infra/main.tf."
  type        = string
  default     = "healthqueue-tofu-state"
}
