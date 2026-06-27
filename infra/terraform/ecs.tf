###############################################################################
# ECS task definitions + Fargate services.
###############################################################################

# --- webapp (public via ALB) ---
resource "aws_ecs_task_definition" "webapp" {
  family                   = "${local.name}-webapp"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name      = "webapp"
    image     = var.image_webapp
    essential = true
    portMappings = [{ containerPort = 8080 }]
    environment  = [{ name = "CONTROL_PLANE_URL", value = local.internal_env.CONTROL_PLANE_URL }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc["webapp"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "webapp"
      }
    }
  }])
}

resource "aws_ecs_service" "webapp" {
  name            = "webapp"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.webapp.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.webapp.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.webapp.arn
    container_name   = "webapp"
    container_port   = 8080
  }
  depends_on = [aws_lb_listener.http]
}

# --- control plane (public subnet, no ALB; reached by webapp via Cloud Map) ---
resource "aws_ecs_task_definition" "controlplane" {
  family                   = "${local.name}-controlplane"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name      = "controlplane"
    image     = var.image_controlplane
    essential = true
    portMappings = [{ containerPort = 8081 }]
    environment = [
      { name = "RUNNER_URL", value = local.internal_env.RUNNER_URL },
      { name = "LLM_URL", value = local.internal_env.LLM_URL },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc["controlplane"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "controlplane"
      }
    }
  }])
}

resource "aws_ecs_service" "controlplane" {
  name            = "controlplane"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.controlplane.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.controlplane.id]
    assign_public_ip = true
  }
  service_registries { registry_arn = aws_service_discovery_service.svc["controlplane"].arn }
}

# --- runner (PRIVATE subnet, NO public IP) ---
resource "aws_ecs_task_definition" "runner" {
  family                   = "${local.name}-runner"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.runner_cpu
  memory                   = var.runner_memory
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name      = "runner"
    image     = var.image_runner
    essential = true
    portMappings = [{ containerPort = 8082 }]
    environment = [
      { name = "LLM_URL", value = local.internal_env.LLM_URL },
      { name = "HEADLESS", value = "true" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc["runner"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "runner"
      }
    }
  }])
}

resource "aws_ecs_service" "runner" {
  name            = "runner"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.runner.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.runner.id]
    assign_public_ip = false # no public inbound, ever
  }
  service_registries { registry_arn = aws_service_discovery_service.svc["runner"].arn }
}

# --- llm (private) ---
resource "aws_ecs_task_definition" "llm" {
  family                   = "${local.name}-llm"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name      = "llm"
    image     = var.image_llm
    essential = true
    portMappings = [{ containerPort = 8083 }]
    secrets = [{
      name      = "OPENROUTER_API_KEY"
      valueFrom = aws_secretsmanager_secret.openrouter.arn
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc["llm"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "llm"
      }
    }
  }])
}

resource "aws_ecs_service" "llm" {
  name            = "llm"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.llm.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.internal.id]
    assign_public_ip = false
  }
  service_registries { registry_arn = aws_service_discovery_service.svc["llm"].arn }
}

###############################################################################
# Public ALB -> webapp
###############################################################################
resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "webapp" {
  name        = "${local.name}-webapp"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"
  health_check {
    path    = "/"
    matcher = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.webapp.arn
  }
}
