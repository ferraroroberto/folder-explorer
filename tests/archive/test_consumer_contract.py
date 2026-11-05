"""The contract with the two apps that drive the batch verbs: every argv shape they send, replayed.

task-os (its archive batch and row actions) and life-os (its shared email
helper) spawn ``email batch <verb>`` and parse the one JSON document it
prints. ``CALLS`` lists every argv shape each sends today; each one is run
through ``main()`` against a fake Outlook, and the test checks what those
callers rely on: the exit code, ``schema_version == 1``, the error code a
refusal carries, and the keys they read. A change that breaks a consumer
fails here before it reaches one. Invented data only.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from app.cli import email_batch as main_batch
from src.email.batch import send
from src.outlook import process
from src.outlook.client import FIND_VIA_RESTRICT, FolderHits, InboxMail
from tests.archive.batch_support import _mail, _seed_index, archive_root, cfg  # noqa: F401 - fixtures are used by name
from tests.archive.draft_support import FakeDraftClient
from tests.archive.test_batch_sent import SentFakeClient, _sent_mail
from tests.archive.test_mailboxes import _registry_doc
from tests.archive.test_send import _client as _draft_client
from tests.archive.test_send import OTHER, _FakeDraft
from tests.archive.test_sync import FakeHandle, _ended

TASK_OS, LIFE_OS = "task-os", "life-os"
START = ["--start-timeout", "60.0"]  # task-os passes its own Outlook start timeout first
INBOX_ID, SENT_ID, REF = "m-1@example.invalid", "s-1@example.invalid", "ref-0001"


@dataclass(frozen=True)
class Call:
    """One argv shape a consumer sends, and what it relies on in the answer."""

    consumer: str
    argv: tuple[str, ...]
    exit: int = 0
    error: Optional[str] = None
    keys: tuple[str, ...] = ()
    # Dotted paths into the first item of a list key, e.g. "mails.candidates".
    item_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    client: str = "mail"
    registry: bool = True


_PLAN_MAIL = ("message_id", "entry_id", "subject", "sender", "recipients", "date_sent", "body_preview",
              "attachment_count", "flag_status", "in_inbox", "already_archived", "candidates")
_CANDIDATE = ("folder_path", "display_name", "score", "match_count", "sample_subjects", "date_prefix")
_APPLY = ("message_id", "folder_path", "ok", "sequence_number", "files", "entry_id", "moved", "categorized",
          "reused", "move_via", "error")
_REVERT = ("message_id", "ok", "deleted", "missing", "refused", "file_errors", "moved_back", "category_removed",
           "entry_id", "index_rows_removed", "error")
_MAILBOX = ("alias", "address", "display_name", "aliases", "archive_folder", "default", "outlook_status")

CALLS: list[Call] = [
    # task-os: the archive batch and the row actions.
    Call(TASK_OS, (*START, "plan", "--candidates", "10"), keys=("mailbox", "counts", "mails", "skipped", "category"),
         item_keys={"mails": _PLAN_MAIL, "mails.candidates": _CANDIDATE}, registry=False),
    Call(TASK_OS, (*START, "apply", "--decisions", "{decisions}"), keys=("counts", "results"),
         item_keys={"results": _APPLY}, registry=False),
    Call(TASK_OS, (*START, "apply", "--decisions", "{decisions}", "--renumber"),
         keys=("results", "renumbered", "renumber_refused"), item_keys={"results": _APPLY}, registry=False),
    Call(TASK_OS, (*START, "revert", "--items", "{items}"), keys=("counts", "results"),
         item_keys={"results": _REVERT}, client="filed", registry=False),
    Call(TASK_OS, (*START, "revert", "--items", "{items}", "--renumber"),
         keys=("results", "renumbered", "renumber_refused"), item_keys={"results": _REVERT}, client="filed",
         registry=False),
    # life-os: the shared email helper (mailtool) and its skills.
    Call(LIFE_OS, ("mailboxes",), keys=("registry", "default", "mailboxes"), item_keys={"mailboxes": _MAILBOX}),
    Call(LIFE_OS, ("plan", "--candidates", "0", "--message-id", INBOX_ID), keys=("mailbox", "filters", "mails"),
         item_keys={"mails": _PLAN_MAIL}),
    Call(LIFE_OS, ("plan", "--candidates", "5", "--mailbox", "owner", "--message-id", INBOX_ID),
         item_keys={"mails.candidates": _CANDIDATE}),
    Call(LIFE_OS, ("plan", "--candidates", "0", "--mailbox", "me", "--search", "budget", "--since", "2026-03-01"),
         keys=("mails", "counts")),
    Call(LIFE_OS, ("plan", "--candidates", "0", "--ref", REF), keys=("mails",)),
    Call(LIFE_OS, ("plan", "--folder", "sent", "--candidates", "0", "--ref", REF, "--since", "2026-03-14T09:00"),
         keys=("mails", "counts"), item_keys={"mails": _PLAN_MAIL}),
    Call(LIFE_OS, ("apply", "--decisions", "{decisions}", "--category", "Filed by an invented skill",
                   "--mailbox", "owner"), keys=("category", "archive_folder", "results"), item_keys={"results": _APPLY}),
    Call(LIFE_OS, ("dismiss", "--message-id", INBOX_ID, "--category", "Dismissed by an invented skill"),
         keys=("archive_folder", "category", "counts", "results")),
    Call(LIFE_OS, ("dismiss", "--message-id", "not-there@example.invalid", "--category", "Dismissed"),
         exit=1, keys=("counts", "results")),
    Call(LIFE_OS, ("draft", "--spec", "{spec}"), keys=("mailbox", "entry_id"), client="draft"),
    Call(LIFE_OS, ("draft", "--spec", "{spec}", "--update", "draft-1"), keys=("mailbox", "entry_id"), client="draft"),
    Call(LIFE_OS, ("read", "--entry-id", "draft-1"),
         keys=("entry_id", "fingerprint", "subject", "to", "cc", "bcc", "attachments"), client="drafts",
         registry=False),
    Call(LIFE_OS, ("send", "--entry-id", "draft-1", "--expect-hash", "{hash}", "{parts}", "--expect-to", OTHER),
         keys=("sent",), client="drafts", registry=False),
    Call(LIFE_OS, ("find", "--query", "budget", "--limit", "25"),
         keys=("mailbox", "hits", "coverage_note", "truncated")),
    Call(LIFE_OS, ("find", "--query", "budget", "--limit", "25", "--since", "2026-03-01",
                   "--folders", "inbox,archive,sent", "--mailbox", "second"), keys=("hits", "truncated")),
    Call(LIFE_OS, ("sync", "--timeout", "120"), keys=("mailboxes", "desktop")),
    Call(LIFE_OS, ("sync", "--timeout", "120", "--mailbox", "owner"), keys=("mailboxes", "desktop")),
    # The refusals life-os passes through by code.
    Call(LIFE_OS, ("plan", "--candidates", "0", "--mailbox", "nobody"), exit=2, error="mailbox_unknown"),
    Call(LIFE_OS, ("send", "--entry-id", "draft-1", "--expect-hash", "0" * 64, "--expect-to", OTHER),
         exit=2, error="approval_mismatch", keys=("error",), client="drafts", registry=False),
    Call(LIFE_OS, ("read", "--entry-id", "gone"), exit=2, error="draft_not_found", client="drafts",
         registry=False),
    Call(LIFE_OS, ("draft", "--spec", "{spec}", "--update", "draft-gone"), exit=2, error="draft_not_found",
         client="draft"),
    Call(LIFE_OS, ("plan", "--candidates", "0"), exit=2, error="mailbox_registry_invalid", client="bad_registry"),
]


# ------------------------------------------------------------- the fakes ---

class ContractClient(SentFakeClient):
    """The batch fake with Sent Items, plus the draft, find and sync surfaces one verb each needs."""

    def __init__(self, mailbox: Any = None) -> None:
        sent = _sent_mail(SENT_ID, "Q3 budget sent", sent=datetime(2026, 3, 14, 10, 0))
        sent.received = None
        inbox = _mail(INBOX_ID, "Q3 budget draft", sent=datetime(2026, 3, 14, 9, 30))
        inbox.received = datetime(2026, 3, 14, 9, 31)
        inbox.headers = "X-Archive-Ref: %s\r\n" % REF
        super().__init__([inbox], [sent])
        self.mailbox = mailbox
        self._drafts = FakeDraftClient()

    def default_account_smtp(self) -> str:
        return "me@example.invalid"

    def create_draft(self, **kw: Any) -> Any:
        return self._drafts.create_draft(**kw)

    def update_draft(self, **kw: Any) -> Any:
        if kw["entry_id"] == "draft-gone":
            from src.outlook.drafts import DRAFT_NOT_FOUND, DraftUpdateError

            raise DraftUpdateError(DRAFT_NOT_FOUND, "no draft with this EntryID")
        return self._drafts.update_draft(**kw)

    def search_folder(self, folder: str, archive_folder: str, dasl: str, matches: Callable[..., bool], *,
                      preview_len: int, limit: int) -> FolderHits:
        hit = InboxMail(message_id="f-%s@example.invalid" % folder, entry_id="e-" + folder, subject="Budget",
                        sender="a@example.invalid", recipients="b@example.invalid",
                        date_sent=datetime(2026, 3, 20), body_preview="budget")
        return FolderHits(folder=folder, name=folder.title(), via=FIND_VIA_RESTRICT, matched=1, mails=[hit])

    def start_sync(self) -> FakeHandle:
        return FakeHandle(_ended())


@pytest.fixture
def replay(cfg, archive_root, tmp_path, monkeypatch, capsys):
    """Run one ``Call`` through ``main()`` and return ``(exit, document)``."""
    monkeypatch.setattr(main_batch, "load_config", lambda: cfg)
    monkeypatch.setattr(main_batch, "setup_logging", lambda: None)
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(
        CoInitialize=lambda: None, CoUninitialize=lambda: None))
    monkeypatch.setattr(process, "windows_session_id", lambda: 1)
    monkeypatch.setattr(process, "get_active_application", lambda: object())
    _seed_index(cfg, archive_root, {"Clients/Northwind": ["Q3 budget review", "Budget notes"]})
    registry_path = tmp_path / "mailboxes.json"
    from src.email import mailboxes

    monkeypatch.setattr(mailboxes, "mailboxes_path", lambda: registry_path)

    def run(call: Call) -> tuple[int, dict[str, Any]]:
        if call.client == "bad_registry":
            registry_path.write_text("{not json", encoding="utf-8")
        elif call.registry:
            registry_path.write_text(json.dumps(_registry_doc()), encoding="utf-8")
        if call.client == "drafts":
            client = _draft_client(monkeypatch, _FakeDraft())
            monkeypatch.setattr(client, "ensure_running", lambda timeout: None)
            factory: Callable[..., Any] = lambda mailbox=None: client  # noqa: E731
        elif call.client == "filed":
            factory = _filed_client(cfg, archive_root, monkeypatch)
        else:
            factory = ContractClient
        monkeypatch.setattr(main_batch, "OutlookClient", factory)
        argv = _fill(call, tmp_path, archive_root, monkeypatch)
        capsys.readouterr()  # only this call's document, not the seeding apply's
        code = main_batch.main(argv)
        return code, json.loads(capsys.readouterr().out)

    return run


def _filed_client(cfg: dict, archive_root: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Any]:
    """A client whose mail is already filed, so ``revert`` has a mail in the archive folder to move back."""
    client = ContractClient()
    monkeypatch.setattr(main_batch, "OutlookClient", lambda mailbox=None: client)
    decisions = archive_root.parent / "seed-decisions.json"
    decisions.write_text(json.dumps([{"message_id": INBOX_ID, "folder_path": str(archive_root / "Filed")}]),
                         encoding="utf-8")
    assert main_batch.main(["apply", "--decisions", str(decisions)]) == 0
    return lambda mailbox=None: client


def _fill(call: Call, tmp_path: Path, archive_root: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The argv with its placeholders filled: input files, and for ``send`` the fingerprint ``read`` gives."""
    out: list[str] = []
    for arg in call.argv:
        if arg == "{decisions}":
            out.append(_write(tmp_path / "decisions.json", [
                {"message_id": INBOX_ID, "folder_path": str(archive_root / "Clients" / "Northwind"),
                 "date_prefix": "auto"}]))
        elif arg == "{items}":
            filed = sorted((archive_root / "Filed").glob("*.msg"))
            out.append(_write(tmp_path / "items.json", [{"message_id": INBOX_ID, "files": [str(p) for p in filed]}]))
        elif arg == "{spec}":
            out.append(_write(tmp_path / "spec.json", {"to": [OTHER], "subject": "Invented", "body_text": "Hello",
                                                       "ref": REF}))
        elif arg in ("{hash}", "{parts}"):
            approval = send.fingerprint(_draft_client(monkeypatch, _FakeDraft()).read_draft("draft-1"))
            if arg == "{hash}":
                out.append(approval["hash"])
            else:
                out.extend("--expect-part=%s=%s" % kv for kv in sorted(approval["parts"].items()))
        else:
            out.append(arg)
    return out


def _write(path: Path, payload: Any) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _first(doc: dict[str, Any], dotted: str) -> dict[str, Any]:
    node: Any = doc
    for key in dotted.split("."):
        assert isinstance(node, dict) and node.get(key), "%s is empty in the document" % dotted
        node = node[key][0]
    return node


@pytest.mark.parametrize("call", CALLS, ids=lambda c: "%s:%s" % (c.consumer, " ".join(c.argv)))
def test_every_consumer_call_keeps_its_contract(replay, call: Call) -> None:
    code, doc = replay(call)

    assert doc["schema_version"] == 1
    assert code == call.exit, doc.get("error")
    if call.error is None:
        assert "error" not in doc, doc.get("error")
    else:
        assert doc["error"]["code"] == call.error
        assert doc["error"]["message"]
    missing = [k for k in call.keys if k not in doc]
    assert missing == [], "keys the consumer reads are gone: %s" % missing
    for dotted, keys in call.item_keys.items():
        item = _first(doc, dotted)
        assert [k for k in keys if k not in item] == [], dotted
    if call.client == "filed":
        assert doc["results"][0]["deleted"] and doc["results"][0]["moved_back"], "revert undid the filing"


def test_the_fixture_list_covers_every_verb_both_consumers_use() -> None:
    verbs = {(c.consumer, next(a for a in c.argv if not a.startswith("-") and a != "60.0")) for c in CALLS}
    assert {v for c, v in verbs if c == TASK_OS} == {"plan", "apply", "revert"}
    assert {v for c, v in verbs if c == LIFE_OS} == {
        "mailboxes", "plan", "apply", "dismiss", "draft", "read", "send", "find", "sync"}


@pytest.mark.parametrize("argv, code, error", [
    (["plan", "--candidates", "0"], 2, "com_unavailable"),
    (["find", "--query", "budget"], 2, "com_unavailable"),
    (["sync", "--timeout", "5"], 2, "com_unavailable"),
    (["mailboxes"], 0, None),
])
def test_without_pywin32_each_verb_says_so_and_never_crashes(cfg, monkeypatch, capsys, argv, code, error):
    monkeypatch.setattr(main_batch, "load_config", lambda: cfg)
    monkeypatch.setattr(main_batch, "setup_logging", lambda: None)
    monkeypatch.setattr(process, "windows_session_id", lambda: 1)
    monkeypatch.setitem(sys.modules, "pythoncom", None)  # importing it raises ImportError

    assert main_batch.main(argv) == code
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1
    if error:
        assert doc["error"]["code"] == error
    else:
        assert {m["outlook_status"] for m in doc["mailboxes"]} == {"unknown"}
