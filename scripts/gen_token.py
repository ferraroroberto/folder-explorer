"""Mint the bearer token a correct sign-in password is exchanged for.

Run it yourself on this PC; it writes ``FOLDER_EXPLORER_TOKEN`` into ``.env``
(gitignored) when none is set yet, and never prints it. ``--rotate`` mints a
new one, which signs every device out. The password itself is set with
``python -m src.password set``.

    .venv\\Scripts\\python.exe scripts\\gen_token.py
"""

from __future__ import annotations

import argparse
import logging
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.secrets_env import TOKEN_VAR, env_path, read_env_file, write_secrets  # noqa: E402

logger = logging.getLogger("gen_token")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(prog="gen_token.py")
    parser.add_argument("--rotate", action="store_true", help="mint a new token (signs every device out)")
    args = parser.parse_args()
    if read_env_file(env_path()).get(TOKEN_VAR) and not args.rotate:
        logger.info("ℹ️ a token is already set; --rotate mints a new one")
        return 0
    write_secrets(env_path(), {TOKEN_VAR: secrets.token_urlsafe(32)})
    logger.info("✅ new token saved to .env. Takes effect on the next sign-in; no restart needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
