"""Where the webapp listens, decided by the one ``remote_access`` setting.

* ``remote_access: false`` binds ``127.0.0.1`` over plain HTTP: no
  certificate, no sign-in, nothing reachable from another device.
* ``remote_access: true`` with the tailnet certificate in
  ``webapp/certificates/`` binds every interface over HTTPS, and callers from
  another device must sign in.
* ``remote_access: true`` without a certificate stays on loopback over HTTP,
  so a token is never sent in clear text over the network; Settings shows
  this state and the command that fixes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.config import PROJECT_ROOT

CERT_DIR = PROJECT_ROOT / "webapp" / "certificates"

STATE_OFF = "off"
STATE_ON = "on"
STATE_NO_CERT = "no-cert"


@dataclass(frozen=True)
class Bind:
    host: str
    port: int
    state: str
    cert: Optional[Path] = None
    key: Optional[Path] = None

    @property
    def scheme(self) -> str:
        return "https" if self.cert else "http"

    @property
    def remote(self) -> bool:
        """True when other devices can reach the app (and so must sign in)."""
        return self.state == STATE_ON

    @property
    def local_url(self) -> str:
        return "%s://127.0.0.1:%d" % (self.scheme, self.port)


def cert_files(cert_dir: Path = CERT_DIR) -> Optional[tuple[Path, Path]]:
    cert, key = cert_dir / "cert.pem", cert_dir / "key.pem"
    return (cert, key) if cert.is_file() and key.is_file() else None


def resolve_bind(cfg: dict[str, Any], cert_dir: Path = CERT_DIR) -> Bind:
    port = int(cfg["port"])
    if not cfg["remote_access"]:
        return Bind("127.0.0.1", port, STATE_OFF)
    files = cert_files(cert_dir)
    if files is None:
        return Bind("127.0.0.1", port, STATE_NO_CERT)
    return Bind("0.0.0.0", port, STATE_ON, files[0], files[1])
