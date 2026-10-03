"""Central security utilities — URL and credential validation.

All outbound HTTP destinations (webhooks, repo clones) must pass
validate_external_url() before any request is made. This guards against
SSRF attacks and credential exfiltration to attacker-controlled servers.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse

log = logging.getLogger(__name__)

# Hostnames that must never be targeted regardless of IP resolution.
_BLOCKED_HOSTS: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",  # nosec B104 — in a blocklist, not binding
    "169.254.169.254",       # AWS / GCP / Azure / DigitalOcean metadata
    "metadata.google.internal",
    "metadata.internal",
})


def _is_private_addr(addr_str: str) -> bool:
    """Return True if *addr_str* resolves to a private/internal IP."""
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return False
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
    )


def _check_resolved_ips(hostname: str, port: int) -> None:
    """Resolve *hostname* and raise ValueError if any result is a private address.

    This is a best-effort DNS rebinding mitigation: it narrows the window
    between URL validation and the actual HTTP request. Network-level egress
    filtering (firewall rules) remains the authoritative control.

    Raises ValueError if resolution fails or any IP is private.
    """
    try:
        results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(
            f"URL hostname {hostname!r} could not be resolved: {exc}"
        ) from exc

    for _family, _type, _proto, _canon, sockaddr in results:
        ip = sockaddr[0]
        if _is_private_addr(ip):
            raise ValueError(
                f"URL hostname {hostname!r} resolves to a private/internal IP: {ip}"
            )


def validate_external_url(url: str, *, allow_http: bool = False, resolve_dns: bool = True) -> None:
    """Raise ValueError if *url* targets a private or internal network endpoint.

    Validation steps:
    1. Scheme must be https (or http if allow_http=True)
    2. Hostname must be present and not in the blocked set
    3. If the hostname is an IP literal, it must not be private/loopback/etc.
    4. If the hostname is a domain name, resolve it via DNS and check all
       returned IPs against the private-range blocklist (resolve_dns=True,
       the default). This narrows the DNS-rebinding window — set resolve_dns=False
       only in unit tests or when an upstream firewall provides the authoritative
       control.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ValueError(f"Invalid URL: {url!r}") from exc

    scheme = (parsed.scheme or "").lower()
    allowed = {"https"} if not allow_http else {"http", "https"}
    if scheme not in allowed:
        hint = "https" if not allow_http else "http or https"
        raise ValueError(f"URL must use {hint} scheme, got {scheme!r}: {url!r}")

    # Strip IPv6 brackets before comparison
    hostname = (parsed.hostname or "").lower().strip("[]")
    if not hostname:
        raise ValueError(f"URL has no hostname: {url!r}")

    if hostname in _BLOCKED_HOSTS:
        raise ValueError(f"URL targets a blocked hostname: {hostname!r}")

    # If the hostname is an IP literal, reject private/reserved ranges immediately.
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        pass  # hostname is a domain name, not an IP literal
    else:
        if _is_private_addr(str(addr)):
            raise ValueError(
                f"URL targets a private/internal/reserved IP address: {addr}"
            )
        return  # IP literal validated — no DNS needed

    # Domain name — resolve and check all returned IPs.
    if resolve_dns:
        port = parsed.port or (443 if scheme == "https" else 80)
        _check_resolved_ips(hostname, port)
