#cloud-config
# Bootstraps a fresh Ubuntu 24.04 Hetzner server for antcrew PROD.
# Rendered by Terraform from main.tf templatefile().

package_update: true
package_upgrade: true
packages:
  - curl
  - ca-certificates
  - gnupg
  - lsb-release
  - fail2ban

runcmd:
  # Docker install (official repo)
  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  - chmod a+r /etc/apt/keyrings/docker.asc
  - echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
  - apt-get update -y
  - apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

  # Mount the persistent data volume at /opt/antcrew
  - mkdir -p ${mount_point}
  - |
    DEVICE="${volume_device}"
    # Wait up to 30 s for the device to appear (volume attachment may lag)
    for i in $(seq 1 30); do
      [ -b "$DEVICE" ] && break
      sleep 1
    done
    if [ -b "$DEVICE" ]; then
      # Format only if the device has no filesystem
      blkid "$DEVICE" || mkfs.ext4 "$DEVICE"
      mount "$DEVICE" "${mount_point}"
      echo "$DEVICE ${mount_point} ext4 defaults,nofail 0 2" >> /etc/fstab
    fi

  # Enable Docker at boot and start it
  - systemctl enable docker
  - systemctl start docker

  # Harden SSH: disable root password auth (key-only)
  - sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
  - sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  - systemctl reload sshd

  # fail2ban for SSH brute-force protection
  - systemctl enable fail2ban
  - systemctl start fail2ban
