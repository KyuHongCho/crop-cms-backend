#!/bin/bash
# Runs once as root on first boot (cloud-init). Output lands in /var/log/cloud-init-output.log.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# First boot can overlap the image's own apt jobs, which hold the apt locks for a while.
apt_get() {
  for _ in 1 2 3 4 5; do
    apt-get -o DPkg::Lock::Timeout=120 "$@" && return 0
    sleep 15
  done
  return 1
}

# The app image builds on this 1 GiB host; swap keeps a memory spike from killing the build.
if [ ! -f /swapfile ]; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

apt_get update
apt_get install -y ca-certificates curl git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL --retry 5 --retry-all-errors https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'SRC'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
SRC
apt_get update
apt_get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker ubuntu

# The production compose file mounts /opt/cms/site read-only; ubuntu must own it to deploy the site.
install -d -o ubuntu -g ubuntu /opt/cms /opt/cms/site
