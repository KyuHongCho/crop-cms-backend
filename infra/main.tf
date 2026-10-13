data "aws_vpc" "default" {
  default = true
}

# Canonical's own account. Its images are the only ones this filter should ever match.
data "aws_ami" "ubuntu" {
  owners      = ["099720109477"]
  most_recent = true

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

resource "aws_key_pair" "admin" {
  key_name   = "cropcms-admin"
  public_key = var.ssh_public_key
}

resource "aws_security_group" "web" {
  name        = "cropcms-web"
  description = "SSH from one address, web traffic from anywhere"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.web.id
  description       = "SSH from the operator"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.ssh_cidr
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.web.id
  description       = "HTTP"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.web.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "https_quic" {
  security_group_id = aws_security_group.web.id
  description       = "HTTP/3"
  ip_protocol       = "udp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.web.id
  description       = "Everything outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_instance" "cms" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.admin.key_name
  vpc_security_group_ids = [aws_security_group.web.id]
  user_data              = file("${path.module}/user_data.sh")
  volume_tags            = local.tags

  # t4g starts in unlimited mode, which bills surplus CPU credits; standard throttles instead.
  credit_specification {
    cpu_credits = "standard"
  }

  # Hop limit 1 keeps containers on the host from reaching the instance metadata service.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 10
    encrypted   = true
  }

  # A newer image or an edited bootstrap script must not replace or restart a server
  # that holds /opt/cms/.env.
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  tags = {
    Name = "cropcms"
  }
}

resource "aws_eip" "cms" {
  domain = "vpc"

  tags = {
    Name = "cropcms"
  }
}

resource "aws_eip_association" "cms" {
  instance_id   = aws_instance.cms.id
  allocation_id = aws_eip.cms.id
}

# Credits are excluded so the alert tracks what the stack would cost, not the net after credits.
resource "aws_budgets_budget" "monthly" {
  name         = "cropcms-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
