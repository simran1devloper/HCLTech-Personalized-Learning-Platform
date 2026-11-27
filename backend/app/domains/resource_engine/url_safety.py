"""SSRF-safe URL validation. Blocks non-http(s), credentials in URL, private/loopback/link-local/metadata IPs,
and hostnames that resolve to them (DNS-rebinding mitigation: every resolved address is checked)."""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from app.core.exceptions import UnsafeURLError

Resolver = Callable[[str], list[str]]
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata"}
_BLOCKED_SUFFIXES = (".internal", ".local", ".localhost", ".lan", ".home.arpa")


def default_resolver(host: str) -> list[str]:
    return list({ai[4][0] for ai in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


def _ip_blocked(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved
            or a.is_unspecified or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10")))


def validate_url(url: str, resolver: Resolver = default_resolver, *, resolve: bool = True) -> str:
    """Returns the normalised URL or raises UnsafeURLError."""
    if not url or len(url) > 2048 or any(c in url for c in "\r\n\t "):
        raise UnsafeURLError("malformed URL")
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https"):
        raise UnsafeURLError("only http(s) URLs are allowed")
    if p.username or p.password:
        raise UnsafeURLError("credentials in URL are not allowed")
    host = (p.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("missing host")
    try:
        port = p.port
    except ValueError:
        raise UnsafeURLError("invalid port")
    if port not in (None, 80, 443, 8080, 8443):
        raise UnsafeURLError("non-standard port not allowed")
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeURLError("internal hostname not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_blocked(str(literal)):
            raise UnsafeURLError("address not allowed")
    elif resolve:
        try:
            addrs = resolver(host)
        except OSError:
            raise UnsafeURLError("host does not resolve")
        if not addrs or any(_ip_blocked(a) for a in addrs):
            raise UnsafeURLError("host resolves to a disallowed address")
    return urlunsplit((p.scheme, host + (f":{port}" if port else ""), p.path or "/", p.query, ""))
