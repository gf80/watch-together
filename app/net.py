"""Защита прокси от запросов во внутреннюю сеть (SSRF)."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".lan", ".home", ".home.arpa", ".localdomain")
BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


class BlockedUrl(ValueError):
    pass


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_blocked_hostname(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_HOST_SUFFIXES):
        return True
    try:
        return _is_blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        return False


async def check_public_url(url: str) -> str:
    from . import config

    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedUrl("поддерживаются только http и https ссылки")
    host = parts.hostname
    if not host:
        raise BlockedUrl("в ссылке нет адреса")
    if config.ALLOW_PRIVATE_PROXY:
        return url
    if is_blocked_hostname(host):
        raise BlockedUrl("обращение к локальным адресам запрещено")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedUrl(f"не удалось разрешить адрес {host}") from exc
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise BlockedUrl("обращение к локальным адресам запрещено")
    return url