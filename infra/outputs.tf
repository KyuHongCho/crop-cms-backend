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
