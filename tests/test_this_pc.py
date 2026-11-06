"""Which client addresses count as this PC: loopback, the PC's own addresses, and nobody else."""

from __future__ import annotations

import socket

import pytest
from starlette.requests import Request

from app.webapp.auth import is_pc_itself
from src.this_pc import is_local_address

# Loopback addresses beyond 127.0.0.1 belong to this machine's loopback interface
# but are not in the fixed loopback set, so they stand in for the PC's own tailnet address.
OWN = "127.0.0.2"
REMOTE = "100.64.0.7"
DOC_NET = "192.0.2.10"


def _request(host: str) -> Request:
    return Request({"type": "http", "client": (host, 50000), "headers": [], "path": "/"})


def test_the_pcs_own_address_is_this_pc() -> None:
    assert is_pc_itself(_request("127.0.0.1")) is True
    assert is_pc_itself(_request(OWN)) is True
    assert is_pc_itself(_request("::ffff:" + OWN)) is True


def test_a_remote_address_is_not_this_pc() -> None:
    assert is_pc_itself(_request(REMOTE)) is False
    assert is_pc_itself(_request(DOC_NET)) is False
    assert is_pc_itself(_request("fd7a:115c:a1e0::1")) is False


@pytest.mark.parametrize("host", ["", "testclient", "not-an-address", "0.0.0.0", "::", "224.0.0.1"])
def test_names_and_wildcards_are_never_this_pc(host: str) -> None:
    assert is_local_address(host) is False


def test_the_machines_own_interface_address_is_local() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((DOC_NET, 9))  # picks the outgoing interface; sends nothing
        mine = probe.getsockname()[0]
    except OSError:
        pytest.skip("no outgoing interface on this machine")
    finally:
        probe.close()
    assert is_local_address(mine) is True
