output "app_url" {
  description = "Public entry point (web app via ALB)."
  value       = "http://${aws_lb.main.dns_name}"
}
