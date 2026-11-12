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


def cert_host(cert: Path) -> Optional[str]:
    """The DNS name the certificate is issued for (the tailnet name), or None when it can't be read or names none."""
    from cryptography import x509  # noqa: PLC0415 - only a certificate read needs it

    try:
        parsed = x509.load_pem_x509_certificate(cert.read_bytes())
        names = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except (OSError, ValueError, x509.ExtensionNotFound):
        return None
    return next((n for n in names if ".ts.net" in n), names[0] if names else None)


def page_base(cfg: dict[str, Any], cert_dir: Path = CERT_DIR) -> str:
    """Where a browser window on this PC opens the app: the certificate's own name over HTTPS, so the page is trusted; else loopback."""
    bind = resolve_bind(cfg, cert_dir)
    host = cert_host(bind.cert) if bind.cert else None
    return "https://%s:%d" % (host, bind.port) if host else bind.local_url
