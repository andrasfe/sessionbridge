###############################################################################
# SessionBridge AWS deployment.
#
# Boundary mapping (mirrors docker-compose):
#   public ALB           -> webapp (only public entry point)
#   webapp/controlplane  -> public subnets, awsvpc tasks
#   runner/llm/artifacts -> PRIVATE subnets, no public IP, no inbound from ALB
#   runner SG            -> ingress ONLY from the control-plane SG
#   artifacts            -> S3 (SSE-KMS) + DynamoDB
#   OpenRouter key       -> Secrets Manager
#   logs                 -> CloudWatch (app already redacts before writing)
#
# Service-to-service DNS is provided by Cloud Map (*.sessionbridge.local).
###############################################################################

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = var.project
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
}

# Single NAT gateway so private tasks (runner) can reach the internet OUTBOUND
# (e.g. load T-Mobile) while remaining unreachable INBOUND.
resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = local.name }
  depends_on    = [aws_internet_gateway.igw]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ---------------------------------------------------------------------------
# Security groups — the heart of the isolation guarantee
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "webapp" {
  name_prefix = "${local.name}-webapp-"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "controlplane" {
  name_prefix = "${local.name}-cp-"
  vpc_id      = aws_vpc.main.id
  # Only the web app may call the control plane.
  ingress {
    from_port       = 8081
    to_port         = 8081
    protocol        = "tcp"
    security_groups = [aws_security_group.webapp.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# THE key rule: the runner accepts inbound ONLY from the control plane.
# Nothing public, nothing from the web app, nothing from the ALB.
resource "aws_security_group" "runner" {
  name_prefix = "${local.name}-runner-"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 8082
    to_port         = 8082
    protocol        = "tcp"
    security_groups = [aws_security_group.controlplane.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"] # outbound to T-Mobile via NAT
  }
}

# LLM + artifacts: reachable only by the control plane and the runner.
resource "aws_security_group" "internal" {
  name_prefix = "${local.name}-internal-"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 8083
    to_port         = 8084
    protocol        = "tcp"
    security_groups = [aws_security_group.controlplane.id, aws_security_group.runner.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------------------
# Service discovery (internal DNS)
# ---------------------------------------------------------------------------
resource "aws_service_discovery_private_dns_namespace" "ns" {
  name = "${local.name}.local"
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "svc" {
  for_each = toset(["controlplane", "runner", "llm", "artifacts"])
  name     = each.key
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.ns.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }
  health_check_custom_config { failure_threshold = 1 }
}

# ---------------------------------------------------------------------------
# Storage + secrets
# ---------------------------------------------------------------------------
resource "aws_kms_key" "artifacts" {
  description             = "${local.name} artifact encryption"
  deletion_window_in_days = 7
}

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${local.name}-artifacts-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.artifacts.arn
    }
  }
}

resource "aws_dynamodb_table" "metadata" {
  name         = "${local.name}-artifact-metadata"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "artifact_id"
  attribute {
    name = "artifact_id"
    type = "S"
  }
}

resource "aws_secretsmanager_secret" "openrouter" {
  name_prefix = "${local.name}/openrouter-"
}

resource "aws_secretsmanager_secret_version" "openrouter" {
  secret_id     = aws_secretsmanager_secret.openrouter.id
  secret_string = var.openrouter_api_key
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name_prefix        = "${local.name}-exec-"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Task role for the artifacts service (S3 + DynamoDB + KMS).
resource "aws_iam_role" "artifacts" {
  name_prefix        = "${local.name}-artifacts-"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "artifacts" {
  role = aws_iam_role.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.artifacts.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.metadata.arn
      },
      {
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = aws_kms_key.artifacts.arn
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# ECS cluster + logging
# ---------------------------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(["webapp", "controlplane", "runner", "llm", "artifacts"])
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = 14
}

# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------
locals {
  internal_env = {
    CONTROL_PLANE_URL = "http://controlplane.${local.name}.local:8081"
    RUNNER_URL        = "http://runner.${local.name}.local:8082"
    LLM_URL           = "http://llm.${local.name}.local:8083"
    ARTIFACT_URL      = "http://artifacts.${local.name}.local:8084"
  }
}
# Task definitions and services live in ecs.tf.
