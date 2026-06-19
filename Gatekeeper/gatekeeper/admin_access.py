from __future__ import annotations

from fastapi import Request

from .config import settings


LOOPBACK_IPS = {'127.0.0.1', '::1'}


def get_mac_from_arp_cache(client_ip: str) -> str | None:
    """Best-effort lookup of a client MAC address from Linux ARP cache."""
    try:
        with open('/proc/net/arp', 'r', encoding='utf-8') as arp_file:
            # Header: IP address HW type Flags HW address Mask Device
            next(arp_file, None)
            for raw_line in arp_file:
                parts = raw_line.split()
                if len(parts) < 4:
                    continue
                if parts[0] != client_ip:
                    continue
                mac = parts[3].strip().upper()
                if mac and mac != '00:00:00:00:00:00':
                    return mac
    except OSError:
        return None
    return None


def can_admin_login_from_request(request: Request) -> tuple[bool, str]:
    if not settings.enforce_admin_mac:
        return True, ''

    if not settings.admin_allowed_macs:
        return False, 'Admin login is blocked: no allowed MAC addresses are configured.'

    client_ip = (request.client.host if request.client else '') or ''
    if client_ip in LOOPBACK_IPS:
        # Loopback traffic is from the host machine running Gatekeeper.
        return True, ''

    client_mac = get_mac_from_arp_cache(client_ip)
    if not client_mac:
        return False, 'Admin login blocked: could not resolve this client MAC address yet.'

    if client_mac not in settings.admin_allowed_macs:
        return False, f'Admin login blocked for MAC {client_mac}.'

    return True, ''
