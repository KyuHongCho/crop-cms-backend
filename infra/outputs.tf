output "elastic_ip" {
  description = "Fixed public address; point the DNS A record here."
  value       = aws_eip.cms.public_ip
}

output "instance_id" {
  value = aws_instance.cms.id
}

output "ssh_command" {
  description = "Copy and run to open a shell on the server."
  value       = "ssh -i ~/.ssh/id_ed25519 ubuntu@${aws_eip.cms.public_ip}"
}
