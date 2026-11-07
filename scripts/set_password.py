"""Set the sign-in password other devices use, and mint the bearer token if there is none.

Run it yourself on this PC; it asks for the password twice and writes
``FOLDER_EXPLORER_PASSWORD`` and ``FOLDER_EXPLORER_TOKEN`` into ``.env``
(gitignored). It never prints either value. ``--rotate-token`` mints a new
token, which signs every device out.

    .venv\\Scripts\\python.exe scripts\\set_password.py
"""

from __future__ import annotations

import argparse
import getpass
import logging
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.secrets_env import (  # noqa: E402
    PASSWORD_VAR,
    TOKEN_VAR,
    env_path,
    read_env_file,
    write_secrets,
)

logger = logging.getLogger("set_password")

_MIN_LENGTH = 8


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(prog="set_password.py")
    parser.add_argument("--rotate-token", action="store_true", help="mint a new token (signs every device out)")
    args = parser.parse_args()
    password = getpass.getpass("New password: ")
    if len(password) < _MIN_LENGTH:
        logger.error("❌ use at least %d characters", _MIN_LENGTH)
        return 1
    if getpass.getpass("Again: ") != password:
        logger.error("❌ the two entries differ; nothing changed")
        return 1
    values = {PASSWORD_VAR: password}
    if args.rotate_token or not read_env_file(env_path()).get(TOKEN_VAR):
        values[TOKEN_VAR] = secrets.token_urlsafe(32)
    write_secrets(env_path(), values)
    logger.info("✅ saved to .env%s. Takes effect on the next sign-in; no restart needed.",
                " with a new token" if TOKEN_VAR in values else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
