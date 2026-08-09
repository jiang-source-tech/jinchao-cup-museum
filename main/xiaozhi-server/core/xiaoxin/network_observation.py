from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import Any


def trusted_proxy_networks(
    config: dict[str, Any],
    *,
    warn_invalid: Callable[[], None] | None = None,
) -> tuple[ipaddress._BaseNetwork, ...]:
    control = config.get("xiaoxin_control", {}) or {}
    overview = control.get("overview_mqtt", {}) or {}
    configured = overview.get("trusted_proxy_cidrs", []) or []
    if isinstance(configured, str):
        configured = [configured]
    networks = []
    invalid = False
    for value in configured:
        try:
            networks.append(ipaddress.ip_network(str(value).strip(), strict=False))
        except ValueError:
            invalid = True
            if warn_invalid is not None:
                warn_invalid()
    return () if invalid else tuple(networks)


def ip_in_networks(address, networks) -> bool:
    return any(
        address.version == network.version and address in network
        for network in networks
    )


def is_public_global_unicast(address) -> bool:
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_unspecified
        and not address.is_reserved
        and not getattr(address, "is_site_local", False)
    )


def observed_public_ip(
    request: Any,
    config: dict[str, Any],
    *,
    warn_invalid: Callable[[], None] | None = None,
) -> str | None:
    try:
        direct_ip = ipaddress.ip_address(str(request.remote or "").strip())
    except ValueError:
        return None

    candidate = direct_ip
    networks = trusted_proxy_networks(config, warn_invalid=warn_invalid)
    if ip_in_networks(direct_ip, networks):
        forwarded_values = request.headers.getall("X-Forwarded-For", [])
        real_ip_values = request.headers.getall("X-Real-IP", [])
        if len(forwarded_values) > 1 or len(real_ip_values) > 1:
            return None
        forwarded = forwarded_values[0].strip() if forwarded_values else ""
        if forwarded:
            try:
                forwarded_ips = [
                    ipaddress.ip_address(value.strip())
                    for value in forwarded.split(",")
                    if value.strip()
                ]
            except ValueError:
                return None
            candidate = next(
                (
                    address
                    for address in reversed(forwarded_ips)
                    if not ip_in_networks(address, networks)
                ),
                None,
            )
            if candidate is None:
                return None
        else:
            real_ip = real_ip_values[0].strip() if real_ip_values else ""
            if real_ip:
                try:
                    candidate = ipaddress.ip_address(real_ip)
                except ValueError:
                    return None

    return str(candidate) if is_public_global_unicast(candidate) else None


__all__ = [
    "ip_in_networks",
    "is_public_global_unicast",
    "observed_public_ip",
    "trusted_proxy_networks",
]
