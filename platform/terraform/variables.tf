variable "hcloud_token" {
  description = "Hetzner Cloud API token (read+write). Set via TF_VAR_hcloud_token or terraform.tfvars."
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "Public SSH key uploaded to Hetzner and authorised on the server."
  type        = string
}

variable "server_type" {
  description = "Hetzner server type. cx22 (2 vCPU / 4 GB) is enough for early prod; upgrade to cx32 or ccx23 when needed."
  type        = string
  default     = "cx22"
}

variable "base_image" {
  description = "Hetzner OS image. Ubuntu 24.04 LTS is recommended."
  type        = string
  default     = "ubuntu-24.04"
}

variable "location" {
  description = "Hetzner datacenter location. nbg1 (Nuremberg) for EU; hel1 (Helsinki) is the nearest GDPR alternative."
  type        = string
  default     = "nbg1"
}

variable "volume_size_gb" {
  description = "Persistent volume size in GB. Stores Docker volumes, database backups, and TraceLog archives."
  type        = number
  default     = 20
}

variable "admin_cidrs" {
  description = "CIDR ranges allowed to SSH to the server. Restrict to your IP or a VPN egress IP."
  type        = list(string)
  default     = ["0.0.0.0/0"]  # tighten this before first deploy
}
