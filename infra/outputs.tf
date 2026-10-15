output "elastic_ip" {
  description = "Fixed public address; point the DNS A record here."
  value       = aws_eip.cms.public_ip
}

output "instance_id" {
  value = aws_instance.cms.id
}

output "ssh_command" {
  description = "Replace PATH_TO_YOUR_PRIVATE_KEY with the private key file that matches ssh_public_key, then run it to open a shell on the server."
  value       = "ssh -i PATH_TO_YOUR_PRIVATE_KEY ubuntu@${aws_eip.cms.public_ip}"
}

output "db_endpoint" {
  description = "Database host name only; this is DB_HOST in /opt/cms/.env. Reachable from the server, not from the internet."
  value       = aws_db_instance.cms.address
}

output "db_name" {
  value = aws_db_instance.cms.db_name
}

output "db_master_username" {
  description = "Administrator role, used only to run db-bootstrap.sql; the app connects as cms_app."
  value       = aws_db_instance.cms.username
}

output "db_master_secret_arn" {
  description = "Secrets Manager entry that holds the administrator password. Read it with the AWS CLI when needed."
  value       = aws_db_instance.cms.master_user_secret[0].secret_arn
}
