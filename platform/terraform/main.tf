terraform {
  required_version = ">= 1.6"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.47"
    }
  }

  # Remote state via Terraform Cloud (free tier) or a local backend.
  # Uncomment and fill in to use Terraform Cloud:
  # cloud {
  #   organization = "antcrew"
  #   workspaces { name = "antcrew-prod" }
  # }
}

provider "hcloud" {
  token = var.hcloud_token
}

# ---------------------------------------------------------------------------
# SSH key — upload your public key once; reuse across recreations.
# ---------------------------------------------------------------------------

resource "hcloud_ssh_key" "github_actions" {
  name       = "github-actions-prod"
  public_key = var.ssh_public_key
}

# ---------------------------------------------------------------------------
# Firewall — only open the ports the platform actually needs.
# ---------------------------------------------------------------------------

resource "hcloud_firewall" "prod" {
  name = "antcrew-prod"

  rule {
    direction = "in"
    protocol  = "tcp"
    port      = "22"
    source_ips = var.admin_cidrs
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # Allow all outbound (default Hetzner behaviour — explicit here for clarity)
  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "any"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "any"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction       = "out"
    protocol        = "icmp"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }
}

# ---------------------------------------------------------------------------
# Persistent volume — survives server recreation (migration, type change).
# ---------------------------------------------------------------------------

resource "hcloud_volume" "data" {
  name      = "antcrew-prod-data"
  size      = var.volume_size_gb
  location  = var.location
  format    = "ext4"
}

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

resource "hcloud_server" "prod" {
  name        = "antcrew-prod"
  server_type = var.server_type
  image       = var.base_image
  location    = var.location

  ssh_keys    = [hcloud_ssh_key.github_actions.id]
  firewall_ids = [hcloud_firewall.prod.id]

  # Cloud-init: install Docker + Docker Compose, mount the data volume,
  # pull GHCR image login handled by the deploy workflow.
  user_data = templatefile("${path.module}/cloud-init.yml.tpl", {
    volume_device = "/dev/disk/by-id/scsi-0HC_Volume_${hcloud_volume.data.id}"
    mount_point   = "/opt/antcrew"
  })

  lifecycle {
    # Prevent accidental server recreation (data lives on the volume, but
    # recreating the server restarts the service and changes the IP).
    prevent_destroy = true
  }
}

# Attach the volume to the server
resource "hcloud_volume_attachment" "data" {
  volume_id = hcloud_volume.data.id
  server_id = hcloud_server.prod.id
  automount = true
}

# ---------------------------------------------------------------------------
# Floating IP — stable public IP; survives server type upgrades.
# ---------------------------------------------------------------------------

resource "hcloud_floating_ip" "prod" {
  name      = "antcrew-prod"
  type      = "ipv4"
  location  = var.location
}

resource "hcloud_floating_ip_assignment" "prod" {
  floating_ip_id = hcloud_floating_ip.prod.id
  server_id      = hcloud_server.prod.id
}
