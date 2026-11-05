"""The .msg reading boundary: one reader module, read in a child with a timeout, inert HTML out."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from src.email.body import ERR_TIMEOUT, fetch_body
from src.email.reader import ERR_GONE, ERR_PARSE
from src.email.sanitize import sanitize_html
from src.email.settings import email_part, is_under
from src.roots import Root

ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "src" / "email" / "reader.py"


def _imports_extract_msg(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "extract_msg" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "extract_msg":
            return True
    return False


def test_only_the_reader_imports_extract_msg() -> None:
    offenders = [p.relative_to(ROOT).as_posix() for folder in ("src", "app", "scripts")
                 for p in (ROOT / folder).rglob("*.py") if p != READER and _imports_extract_msg(p)]
    assert offenders == []
    assert _imports_extract_msg(READER)


def _runner(stdout: str = "", raises: bool = False):
    def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        assert cmd[1:3] == ["-m", "src.email.reader"]
        if raises:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(cmd, 0, stdout, "")
    return run


def test_fetch_body_tells_each_failure_apart() -> None:
    assert fetch_body("x.msg", run=_runner(raises=True)).error == ERR_TIMEOUT
    gone = fetch_body("x.msg", run=_runner(json.dumps({"ok": False, "error": ERR_GONE, "message": "m"})))
    parse = fetch_body("x.msg", run=_runner(json.dumps({"ok": False, "error": ERR_PARSE, "message": "m"})))
    assert (gone.error, parse.error) == (ERR_GONE, ERR_PARSE) and gone.message != parse.message
    assert fetch_body("x.msg", run=_runner("not json")).error == "read"


def test_fetch_body_returns_text_and_inert_html() -> None:
    html = '<p onclick="x()">Hi <img src="https://tracker.example/p.gif"><script>alert(1)</script></p>'
    body = fetch_body("x.msg", run=_runner(json.dumps({"ok": True, "text": "Hi", "html": html})))
    assert body.ok and body.text == "Hi"
    assert "script" not in body.html and "onclick" not in body.html and "tracker" not in body.html


def test_sanitize_keeps_layout_and_drops_everything_active() -> None:
    clean = sanitize_html(
        '<table border="1"><tr><td style="color:red">a &lt; b</td></tr></table>'
        '<a href="javascript:evil()">x</a><a href="https://example.org">y</a>'
        '<img src="data:image/png;base64,AAAA" alt="i"><iframe src="https://example.org"></iframe>'
        '<style>p { color: blue }</style><p>after</p><form><input name="q"></form>')
    assert '<td style="color:red">a &lt; b</td>' in clean
    assert "javascript" not in clean and 'href="https://example.org"' in clean
    assert 'src="data:image/png' in clean and "iframe" not in clean and "<form" not in clean and "input" not in clean
    assert "<style>p { color: blue }</style><p>after</p>" in clean


def test_email_folders_must_sit_inside_a_scan_root(tmp_path: Path) -> None:
    index = tmp_path / "emails.db"
    index.write_bytes(b"")
    roots = [Root("inv", "Invented", str(tmp_path / "Drive"), "local")]
    inside = email_part({"email": {"index_path": str(index), "roots": [str(tmp_path / "Drive" / "Mail")]}}, roots)
    outside = email_part({"email": {"index_path": str(index), "roots": [str(tmp_path / "Elsewhere")]}}, roots)
    off = email_part({"email": {"index_path": None, "roots": []}}, roots)
    assert inside.ready and inside.roots and not inside.own
    assert outside.roots == [] and "outside every scan root" in str(outside.reader_problem)
    assert not off.ready and "email.roots" in str(off.problem) and off.own
    assert "never writes" in str(inside.stage_problem)
    assert is_under(str(tmp_path / "Drive" / "Mail" / "a.msg"), str(tmp_path / "drive"))
    assert not is_under(str(tmp_path / "Drive2"), str(tmp_path / "Drive"))
