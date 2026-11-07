"""The window's colours and type, read from the web app's own ``tokens.css`` so the two cannot drift.

``:root`` is the light theme and ``[data-theme="dark"]`` overrides it, exactly as the
page applies them. The window follows the Windows app theme unless told which to use.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from typing import Optional

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

TOKENS_CSS = PROJECT_ROOT / "app" / "webapp" / "static" / "tokens.css"
_BLOCK = re.compile(r'(:root|\[data-theme="dark"\])\s*\{(.*?)\n\}', re.DOTALL)
_DECL = re.compile(r"--([\w-]+):\s*([^;]+);")
_VAR = re.compile(r"var\(--([\w-]+)\)")
_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

FONT = "Segoe UI"
MONO = "Consolas"
# Points for the app's type scale (body 1rem, body-sm and label 0.875rem, caption 0.75rem at 96 dpi), and the subject's heading size.
SIZE_BODY, SIZE_SMALL, SIZE_CAPTION, SIZE_TITLE = 11, 10, 9, 13
# The step the page uses between controls and inside a card.
SPACE_SM, SPACE_MD = 8, 16


@dataclass(frozen=True)
class Palette:
    bg: str
    card: str
    card_off: str
    ink: str
    muted: str
    line: str
    line_muted: str
    accent: str
    accent_fg: str
    accent_fill: str
    accent_text: str
    attention_text: str
    success: str
    danger_text: str
    accent_soft: str
    neutral_soft: str
    attention_soft: str
    dark: bool


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def blend(color: str, over: str, percent: int) -> str:
    """``color`` at ``percent`` opacity over ``over``: what ``color-mix(in srgb, color N%, transparent)`` shows on that surface."""
    fg, bg = _hex(color), _hex(over)
    return "#%02x%02x%02x" % tuple(round(f * percent / 100 + b * (100 - percent) / 100) for f, b in zip(fg, bg))


def read_tokens(css: str, dark: bool) -> dict[str, str]:
    """The ``--name: value`` pairs of the light theme, with the dark block over them when ``dark``; ``var()`` is resolved."""
    blocks = {name: dict(_DECL.findall(body)) for name, body in _BLOCK.findall(css)}
    tokens = dict(blocks.get(":root", {}))
    if dark:
        tokens.update(blocks.get('[data-theme="dark"]', {}))
    return {k: _VAR.sub(lambda m: tokens.get(m.group(1), m.group(0)), v.strip()) for k, v in tokens.items()}


def windows_dark() -> bool:
    """True when Windows is set to dark app mode; False when it is light or the setting can't be read."""
    if sys.platform != "win32":
        return False
    import winreg  # noqa: PLC0415 - Windows only

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        logger.info("ℹ️ archive window: the Windows app theme isn't readable, so it opens light")
        return False


def palette(dark: Optional[bool] = None) -> Palette:
    """The palette for the light or dark theme; ``None`` follows Windows."""
    dark = windows_dark() if dark is None else dark
    t = read_tokens(TOKENS_CSS.read_text(encoding="utf-8"), dark)
    return Palette(
        bg=t["bg"], card=t["card"], card_off=t["card-off"], ink=t["ink"], muted=t["muted"], line=t["line"],
        line_muted=t["line-muted"], accent=t["accent"], accent_fg=t["accent-fg"], accent_fill=t["accent-fill"],
        accent_text=t["accent-text"], attention_text=t["attention-text"], success=t["success"], danger_text=t["danger-text"],
        accent_soft=blend(t["accent"], t["card"], 16), neutral_soft=blend(t["muted"], t["card"], 16),
        attention_soft=blend(t["attention"], t["card"], 16), dark=dark)
