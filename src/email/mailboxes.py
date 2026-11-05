"""The mailbox registry: every Outlook mailbox the batch verbs may act on, from ``mailboxes.json``.

The file sits beside ``config.json``, machine-local and gitignored, because it
holds personal addresses and this repo is public; ``mailboxes.sample.json`` is
the tracked template. No file means one synthesized mailbox resolved to
Outlook's default store. A file that is present but malformed is an error:
falling back to the default store then could act on the wrong mailbox.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src import config

logger = logging.getLogger(__name__)

MAILBOXES_NAME = "mailboxes.json"
MAILBOXES_SCHEMA_VERSION = 1
# The alias of the one mailbox synthesized when there is no registry file.
SYNTHESIZED_ALIAS = "default"


def mailboxes_path() -> Path:
    """The registry file, beside ``config.json`` (which ``FOLDER_EXPLORER_CONFIG`` may move)."""
    return config.config_path().parent / MAILBOXES_NAME


class MailboxRegistryError(ValueError):
    """``mailboxes.json`` exists but is not a usable registry."""


class MailboxUnknownError(LookupError):
    """A mailbox name that is neither an alias nor an extra alias in the registry."""


_MAILBOX_KEYS = {"address", "display_name", "aliases", "archive_folder"}
_REGISTRY_KEYS = {"schema_version", "default", "mailboxes"}


@dataclass(frozen=True)
class Mailbox:
    """One mailbox in the registry.

    ``synthesized`` is true only for the single mailbox made up when there is
    no registry file: its ``address`` is empty and the Outlook client resolves
    it to the profile's default store, which is today's behaviour. A registry
    mailbox is always resolved by its ``address`` and never by the default store.
    """

    alias: str
    address: str = ""
    display_name: str = ""
    aliases: tuple[str, ...] = ()
    archive_folder: str | None = None   # None = outlook.archive_folder
    synthesized: bool = False


@dataclass(frozen=True)
class MailboxRegistry:
    """Every mailbox this install knows, and which one a verb uses by default."""

    default: str
    mailboxes: dict[str, Mailbox] = field(default_factory=dict)
    synthesized: bool = False

    def select(self, name: str | None = None) -> Mailbox:
        """The mailbox called ``name`` (an alias or extra alias, any case),
        or the default one for ``None``.

        Raises:
            MailboxUnknownError: ``name`` matches nothing in the registry.
        """
        if name is None:
            return self.mailboxes[self.default]
        wanted = name.strip().casefold()
        for mailbox in self.mailboxes.values():
            if wanted == mailbox.alias.casefold() or wanted in (a.casefold() for a in mailbox.aliases):
                return mailbox
        raise MailboxUnknownError(
            f"no mailbox called {name!r} in the registry; known: {', '.join(self.mailboxes)}"
        )


def _synthesized_registry() -> MailboxRegistry:
    mailbox = Mailbox(alias=SYNTHESIZED_ALIAS, synthesized=True)
    return MailboxRegistry(
        default=SYNTHESIZED_ALIAS, mailboxes={SYNTHESIZED_ALIAS: mailbox}, synthesized=True,
    )


def _optional_str(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key, "")
    if not isinstance(value, str):
        raise MailboxRegistryError(f"{where}: {key} must be a string")
    return value.strip()


def _parse_mailbox(alias: str, entry: Any) -> Mailbox:
    where = f"mailboxes.{alias}"
    if not isinstance(entry, dict):
        raise MailboxRegistryError(f"{where} must be an object")
    unknown = sorted(set(entry) - _MAILBOX_KEYS)
    if unknown:
        # A misspelt archive_folder would otherwise file into "Archive" silently.
        raise MailboxRegistryError(f"{where}: unknown key(s) {', '.join(unknown)}")
    address = _optional_str(entry, "address", where)
    if "@" not in address:
        raise MailboxRegistryError(f"{where}: address must be an e-mail address")
    aliases = entry.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
        raise MailboxRegistryError(f"{where}: aliases must be a list of non-blank strings")
    archive_folder = _optional_str(entry, "archive_folder", where)
    if "archive_folder" in entry and not archive_folder:
        raise MailboxRegistryError(f"{where}: archive_folder must not be blank")
    return Mailbox(
        alias=alias,
        address=address,
        display_name=_optional_str(entry, "display_name", where),
        aliases=tuple(a.strip() for a in aliases),
        archive_folder=archive_folder or None,
    )


def parse_mailbox_registry(data: Any) -> MailboxRegistry:
    """Validate a registry document into a :class:`MailboxRegistry`.

    Raises:
        MailboxRegistryError: the document is not a usable registry; the
            message names the offending key.
    """
    if not isinstance(data, dict):
        raise MailboxRegistryError("the registry must be a JSON object")
    unknown = sorted(set(data) - _REGISTRY_KEYS)
    if unknown:
        raise MailboxRegistryError(f"unknown top-level key(s) {', '.join(unknown)}")
    if data.get("schema_version") != MAILBOXES_SCHEMA_VERSION:
        raise MailboxRegistryError(
            f"schema_version must be {MAILBOXES_SCHEMA_VERSION}, got {data.get('schema_version')!r}"
        )
    raw = data.get("mailboxes")
    if not isinstance(raw, dict) or not raw:
        raise MailboxRegistryError("mailboxes must be a non-empty object")

    mailboxes: dict[str, Mailbox] = {}
    names: set[str] = set()
    addresses: set[str] = set()
    for alias, entry in raw.items():
        if not alias.strip():
            raise MailboxRegistryError("a mailbox alias must not be blank")
        mailbox = _parse_mailbox(alias.strip(), entry)
        # Every name must pick exactly one mailbox, or --mailbox could act on
        # the wrong one.
        for name in (mailbox.alias, *mailbox.aliases):
            if name.casefold() in names:
                raise MailboxRegistryError(f"the name {name!r} is used by more than one mailbox")
            names.add(name.casefold())
        if mailbox.address.casefold() in addresses:
            raise MailboxRegistryError(f"mailboxes.{alias}: address is used by another mailbox")
        addresses.add(mailbox.address.casefold())
        mailboxes[mailbox.alias] = mailbox

    default = data.get("default")
    if not isinstance(default, str) or default.strip() not in mailboxes:
        raise MailboxRegistryError(f"default must name one of: {', '.join(mailboxes)}")
    return MailboxRegistry(default=default.strip(), mailboxes=mailboxes)


def load_mailbox_registry(path: Optional[Path] = None) -> MailboxRegistry:
    """Load ``mailboxes.json``, or synthesize today's single mailbox.

    No file is not an error: the registry is one mailbox resolved to Outlook's
    default store, which is what every verb did before the registry existed.
    A file that is present but unreadable or malformed *is* an error — falling
    back to the default store then could act on the wrong mailbox.

    Raises:
        MailboxRegistryError: the file exists but is not a usable registry.
    """
    path = path or mailboxes_path()
    if not path.exists():
        logger.info(
            "ℹ️ No %s; using Outlook's default store as the only mailbox.", path.name
        )
        return _synthesized_registry()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise MailboxRegistryError(f"{path}: {type(exc).__name__}: {exc}") from exc
    try:
        return parse_mailbox_registry(data)
    except MailboxRegistryError as exc:
        raise MailboxRegistryError(f"{path}: {exc}") from exc
