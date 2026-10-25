# OpenTofu only evaluates outputs after every variable validation has passed,
# so this message appearing means the inputs were accepted.
output "validation_status" {
  description = "Confirms that all input variable validations passed."
  value       = "All variable validations passed (environment = ${var.environment}, region = ${var.region})."
}

output "bucket_name" {
  description = "Name of the S3 bucket."
  value       = aws_s3_bucket.main.bucket
}
