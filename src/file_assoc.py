"""Settings' "Associate Markdown files with this app": the "Open with" handler for ``.md`` and ``.markdown``, per user.

Every key lives under ``HKCU\\Software\\Classes``: never HKLM, never anything
that needs an administrator. Turning it on writes a ProgId whose open command
starts the launcher (``app/launcher.py``), lists that ProgId under each
extension's ``OpenWithProgids``, and adds "Open in Folder Explorer" to each
extension's File Explorer menu. Turning it off removes exactly those keys and
values and nothing else. Windows still decides the default app for ``.md``;
it may ask the user once to pick it.

The state is read back from the registry each time: ``on`` (every key there
with this checkout's command), ``off`` (none of them), ``partial`` (some, or
another checkout's command) or ``unknown`` (the registry couldn't be read, or
this instance is not allowed to touch it). Tests pass a fake :class:`Registry`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional, Protocol

from src.config import PROJECT_ROOT
from src.desktop import desktop_off

logger = logging.getLogger(__name__)

PROG_ID = "FolderExplorer.Markdown"
EXTENSIONS = (".md", ".markdown")
MENU_VERB = "FolderExplorer"
MENU_TEXT = "Open in Folder Explorer"
DOC_TYPE_TEXT = "Markdown document"

STATES = ("on", "off", "partial", "unknown")


class NotAllowed(Exception):
    """This instance never touches the registry: not Windows, or opening things is off (the e2e suite)."""


class Registry(Protocol):
    """The four calls under ``HKCU\\Software\\Classes``; keys are relative to it."""

    def read(self, key: str, name: str) -> Optional[str]:
        """The value's text, or None when the key or the value is missing; ``OSError`` when it can't be read."""

    def write(self, key: str, name: str, value: str) -> None:
        """Set a text value, creating the key."""

    def delete_value(self, key: str, name: str) -> None:
        """Remove one value; a missing one is fine."""

    def delete_tree(self, key: str) -> None:
        """Remove a key and everything below it; a missing one is fine."""


class WinRegistry:
    """The real registry, the current user's classes only."""

    _BASE = "Software\\Classes\\"

    def read(self, key: str, name: str) -> Optional[str]:
        import winreg  # noqa: PLC0415 - Windows only

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._BASE + key) as k:
                value, _kind = winreg.QueryValueEx(k, name)
        except FileNotFoundError:
            return None
        return "" if value is None or isinstance(value, bytes) else str(value)

    def write(self, key: str, name: str, value: str) -> None:
        import winreg  # noqa: PLC0415

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self._BASE + key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete_value(self, key: str, name: str) -> None:
        import winreg  # noqa: PLC0415

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._BASE + key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass

    def delete_tree(self, key: str) -> None:
        import winreg  # noqa: PLC0415

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._BASE + key, 0,
                                winreg.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        child = winreg.EnumKey(k, 0)
                    except OSError:
                        break
                    self.delete_tree(key + "\\" + child)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self._BASE + key)
        except FileNotFoundError:
            pass


def command(python: Optional[Path] = None, repo: Path = PROJECT_ROOT) -> str:
    """The open command: this checkout's windowless Python running the launcher on the file Windows passes."""
    exe = python or Path(sys.executable).with_name("pythonw.exe")
    if not exe.is_file():
        exe = Path(sys.executable)
    # Windows starts the handler in no particular folder, so the checkout goes on the import path first.
    boot = "import runpy,sys;sys.path.insert(0,%r);runpy.run_module('app.launcher',run_name='__main__')" % str(repo)
    return '"%s" -c "%s" "%%1"' % (exe, boot)


def _values(cmd: str) -> list[tuple[str, str, str]]:
    """Every ``(key, value name, text)`` the association sets; ``""`` is a key's default value."""
    out = [(PROG_ID, "", DOC_TYPE_TEXT), (PROG_ID + "\\shell\\open\\command", "", cmd)]
    for ext in EXTENSIONS:
        menu = "SystemFileAssociations\\" + ext + "\\shell\\" + MENU_VERB
        out += [(ext + "\\OpenWithProgids", PROG_ID, ""), (menu, "", MENU_TEXT), (menu + "\\command", "", cmd)]
    return out


def _allowed() -> bool:
    """The registry is touched only on Windows, and never by an instance with opening things off (the e2e suite)."""
    return sys.platform == "win32" and not desktop_off()


def state(reg: Optional[Registry] = None, cmd: Optional[str] = None) -> str:
    """``on``, ``off``, ``partial`` or ``unknown``, read back from the registry."""
    if reg is None and not _allowed():
        return "unknown"
    reg = reg or WinRegistry()
    wanted = _values(cmd or command())
    try:
        found = [reg.read(key, name) for key, name, _ in wanted]
    except OSError as exc:
        logger.warning("⚠️ file association: the registry couldn't be read: %s", exc)
        return "unknown"
    if all(v is None for v in found):
        return "off"
    return "on" if all(v == text for v, (_, _, text) in zip(found, wanted)) else "partial"


def _notify_shell() -> None:
    """Tell File Explorer the associations changed, so its menus follow without a sign-out."""
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED, SHCNF_IDLIST


def set_on(on: bool, reg: Optional[Registry] = None, cmd: Optional[str] = None) -> str:
    """Add or remove the association, then the state read back. :class:`NotAllowed` when this instance may not."""
    if reg is None and not _allowed():
        raise NotAllowed("File associations can't be changed by this instance")
    real = reg is None
    reg = reg or WinRegistry()
    cmd = cmd or command()
    if on:
        for key, name, text in _values(cmd):
            reg.write(key, name, text)
    else:
        reg.delete_tree(PROG_ID)
        for ext in EXTENSIONS:
            reg.delete_value(ext + "\\OpenWithProgids", PROG_ID)
            reg.delete_tree("SystemFileAssociations\\" + ext + "\\shell\\" + MENU_VERB)
    if real:
        _notify_shell()
    logger.info("ℹ️ file association: Markdown %s for this user", "associated" if on else "no longer associated")
    return state(reg, cmd)
