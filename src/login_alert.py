"""The owner alert for repeated wrong passwords: its text and its Telegram notifier.

The lock-out (``src/login_lockout.py``) calls back once per guessing streak; this
module turns that into one short message for the ``coding alerts`` chat. The
message carries the app name, the client address and the failure count, never
a password or a token.
"""

from __future__ import annotations

from src.login_lockout import LockoutAlert
from src.notify import Notifier, TelegramConfig, build_notifier
from src.secrets_env import TELEGRAM_CHAT_VAR, TELEGRAM_TOKEN_VAR, get_secret

APP_NAME = "folder-explorer"


def alert_text(alert: LockoutAlert) -> str:
    held = f" (+{alert.suppressed} more held back)" if alert.suppressed else ""
    return f"🚨 {APP_NAME}: {alert.failures} wrong passwords in a row from {alert.key}{held}"


def alert_notifier() -> Notifier | None:
    """The Telegram notifier from ``.env``, or None when the bot or the chat is not set."""
    token, chat = get_secret(TELEGRAM_TOKEN_VAR), get_secret(TELEGRAM_CHAT_VAR)
    return build_notifier("telegram" if token and chat else "none", TelegramConfig(token, chat))
