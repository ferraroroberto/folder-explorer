"""Is a client address one of this machine's own addresses?

A caller that reaches the app through the PC's own tailnet or LAN address
arrives from that address, not from loopback, yet it is still this PC.
The operating system answers "is this address mine" exactly: a socket can bind
only an address that belongs to one of the machine's interfaces. Nothing is
sent and nothing listens, and the answer follows address changes with no list
to keep fresh.
"""

from __future__ import annotations

import ipaddress
import socket


def is_local_address(host: str) -> bool:
    """True when ``host`` is an IP address assigned to an interface of this machine."""
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # Binding the wildcard or a multicast group succeeds without naming an interface.
    if ip.is_unspecified or ip.is_multicast:
        return False
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as probe:
            probe.bind((str(ip), 0))
    except OSError:
        return False
    return True
