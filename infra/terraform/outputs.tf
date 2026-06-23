output "app_url" {
  description = "Public entry point (web app via ALB)."
  value       = "http://${aws_lb.main.dns_name}"
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "metadata_table" {
  value = aws_dynamodb_table.metadata.name
}
