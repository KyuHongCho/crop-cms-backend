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

variable "monthly_budget_usd" {
  description = "Monthly budget in USD. Alerts: actual spend over 80 percent of it, forecast over 100 percent."
  type        = number
  default     = 25
}
