variable "ssh_cidr" {
  description = "Your public IPv4 address as a /32, for example 203.0.113.7/32. The only source allowed to reach SSH."
  type        = string

  validation {
    condition = (
      can(regex("^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])/32$", var.ssh_cidr))
      && var.ssh_cidr != "0.0.0.0/32"
    )
    error_message = "ssh_cidr must be one IPv4 address written as a /32, such as 203.0.113.7/32. Ranges and 0.0.0.0/0 are refused."
  }
}

variable "ssh_public_key" {
  description = "The PUBLIC half of your ed25519 key (the contents of id_ed25519.pub). Never the private key."
  type        = string

  validation {
    condition     = can(regex("^ssh-ed25519 AAAA[0-9A-Za-z+/]+={0,3}( [^\\n]*)?$", var.ssh_public_key))
    error_message = "ssh_public_key must be a single-line ed25519 public key starting with 'ssh-ed25519 AAAA'."
  }
}

variable "budget_email" {
  description = "Address that receives the budget alert."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.budget_email))
    error_message = "budget_email must look like name@example.com."
  }
}

variable "instance_type" {
  description = "EC2 size. Must be a Graviton (ARM) type because the image is arm64."
  type        = string
  default     = "t4g.micro"

  validation {
    condition     = can(regex("^[a-z]+[0-9]+g[a-z]*\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must be an ARM (Graviton) type such as t4g.micro or t4g.small."
  }
}

variable "db_backup_retention_days" {
  description = "Days of automated backups and point-in-time recovery. 0 is refused because it switches both off. 1 is the most the Free plan accepted on a real apply; a Paid plan may allow more (untested)."
  type        = number
  default     = 1

  validation {
    condition     = var.db_backup_retention_days >= 1 && var.db_backup_retention_days <= 35 && var.db_backup_retention_days == floor(var.db_backup_retention_days)
    error_message = "db_backup_retention_days must be a whole number from 1 to 35."
  }
}

variable "db_deletion_protection" {
  description = "Blocks terraform destroy of the database. Turn it off, apply, then destroy, only to remove the database on purpose."
  type        = bool
  default     = true
}

variable "db_final_snapshot_name" {
  description = "Name of the snapshot taken when the database is destroyed. A second destroy fails while an old snapshot has this name."
  type        = string
  default     = "cropcms-final"

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9-]{0,254}$", var.db_final_snapshot_name)) && !can(regex("--|-$", var.db_final_snapshot_name))
    error_message = "db_final_snapshot_name must be 1-255 letters, digits or hyphens, start with a letter, not end with a hyphen and not contain two hyphens in a row."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly budget in USD. Alerts: actual spend over 80 percent of it, forecast over 100 percent."
  type        = number
  default     = 25
}
