output "server_ipv4" {
  description = "Primary IPv4 address of the prod server (changes on recreation — use floating_ip instead)."
  value       = hcloud_server.prod.ipv4_address
}

output "floating_ip" {
  description = "Stable floating IPv4 — point antcrew.org A record here."
  value       = hcloud_floating_ip.prod.ip_address
}

output "volume_id" {
  description = "Persistent data volume ID — reference when resizing or snapshotting."
  value       = hcloud_volume.data.id
}

output "server_id" {
  description = "Hetzner server ID — used by the UAT self-delete script and hcloud CLI."
  value       = hcloud_server.prod.id
}
