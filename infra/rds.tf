# RDS needs subnets in at least two zones even for one Single-AZ instance.
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Admits only the web group (a reference, not a CIDR), so a replaced server keeps its access.
resource "aws_security_group" "db" {
  name        = "cropcms-db"
  description = "PostgreSQL from the web server only"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_web" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from the web server"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.web.id
}

resource "aws_db_subnet_group" "cms" {
  name       = "cropcms"
  subnet_ids = data.aws_subnets.default.ids
}

# Pins TLS-only connections in code rather than relying on the engine default.
resource "aws_db_parameter_group" "cms" {
  name   = "cropcms-pg17"
  family = "postgres17"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

# RDS generates and stores the master password in Secrets Manager, so it never enters state or a
# plan file. The app does not use this role; it uses cms_app (db-bootstrap.sql).
resource "aws_db_instance" "cms" {
  identifier     = "cropcms"
  engine         = "postgres"
  engine_version = "17"
  instance_class = "db.t4g.micro"

  db_name                     = "cms"
  username                    = "cms_master"
  manage_master_user_password = true

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_subnet_group_name   = aws_db_subnet_group.cms.name
  parameter_group_name   = aws_db_parameter_group.cms.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period   = var.db_backup_retention_days
  copy_tags_to_snapshot     = true
  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = var.db_final_snapshot_name

  auto_minor_version_upgrade = true
  # A later change to the class, engine version or storage type is applied at once, not in the
  # maintenance window; on this single-AZ instance one that reboots means downtime.
  apply_immediately = true
}
