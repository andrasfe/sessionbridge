variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "sessionbridge"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

# Container images (push to ECR first). One per service.
variable "image_webapp" { type = string }
variable "image_controlplane" { type = string }
variable "image_runner" { type = string }
variable "image_llm" { type = string }
variable "image_artifacts" { type = string }

variable "openrouter_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

# Fargate runner needs more CPU/mem for Chromium.
variable "runner_cpu" {
  type    = number
  default = 1024
}
variable "runner_memory" {
  type    = number
  default = 2048
}
