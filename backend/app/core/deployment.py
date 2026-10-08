"""Explicit public origin and cookie policy for a single-service deployment."""
from dataclasses import dataclass
import ipaddress
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DeploymentSettings:
    public_origin: str | None
    cookie_secure: bool


def deployment_settings() -> DeploymentSettings:
    origin = os.getenv("AMAN_PUBLIC_ORIGIN", "").strip().rstrip("/") or None
    if origin:
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment or any(char.isspace() for char in origin)):
            raise ValueError("AMAN_PUBLIC_ORIGIN must be one http(s) origin without a path")
        # Accessing port also validates invalid/out-of-range port strings.
        parsed.port
        if origin != f"{parsed.scheme}://{parsed.netloc}" or "*" in origin:
            raise ValueError("AMAN_PUBLIC_ORIGIN must be one exact origin")
    secure = os.getenv("AMAN_COOKIE_SECURE", "").strip().lower()
    if secure not in {"", "true", "false", "1", "0"}:
        raise ValueError("AMAN_COOKIE_SECURE must be true or false")
    return DeploymentSettings(origin, secure in {"true", "1"} or bool(origin and origin.startswith("https://")))


def secure_cookie(request) -> bool:
    return deployment_settings().cookie_secure or request.url.scheme == "https"


def trusted_proxy_ips() -> str:
    value = os.getenv("AMAN_TRUSTED_PROXY_IPS", "127.0.0.1").strip()
    if not value:
        return ""
    entries = [part.strip() for part in value.split(",")]
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry, strict=False)
            if network.prefixlen == 0:
                raise ValueError("Unrestricted proxy networks are forbidden")
        except ValueError as exc:
            raise ValueError("AMAN_TRUSTED_PROXY_IPS accepts explicit IP addresses/CIDRs; wildcard trust is forbidden") from exc
    return ",".join(entries)
