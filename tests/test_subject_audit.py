"""The dry run that lists archived mails whose name lost its first letters: invented names, a fake reader, nothing renamed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.email.manifest import Manifest, MsgFile
from src.email.reader import ReadError
from src.email.subject_audit import audit, cut_name, describe

MB = 1024 * 1024


@pytest.mark.parametrize(
    ("filename", "subject", "expected"),
    [
        ("2026-11-02 - 017 - cibo de pago de Tesla.msg", "Recibo de pago de Tesla",
         "2026-11-02 - 017 - Recibo de pago de Tesla.msg"),
        ("004 - visión anual.msg", "Revisión anual", "004 - Revisión anual.msg"),
        ("012 - port for March.msg", "Report for March", "012 - Report for March.msg"),
    ],
)
def test_a_cut_name_is_found_and_its_whole_name_given(filename: str, subject: str, expected: str) -> None:
    assert cut_name(filename, subject) == expected


@pytest.mark.parametrize(
    ("filename", "subject"),
    [
        ("2026-11-02 - 017 - Recibo de pago de Tesla.msg", "Recibo de pago de Tesla"),
        ("005 - Plan of action.msg", "Plan of action"),
        ("006 - Other thing.msg", "Recibo de pago"),
        ("not numbered.msg", "Recibo de pago"),
        ("007 - Recibo.msg", ""),
    ],
)
def test_a_whole_name_or_an_unrelated_one_is_left_alone(filename: str, subject: str) -> None:
    assert cut_name(filename, subject) is None


LONG = "Recibo de pago de Tesla del mes de marzo de dos mil veintiseis"


@pytest.mark.parametrize(
    ("filename", "subject", "expected"),
    [
        # The whole stem is compared, not its first 24 characters: a long name is found too.
        ("2026-03-02 - 009 - cibo de pago de Tesla del mes de marzo de dos mil veintiseis.msg", LONG,
         "2026-03-02 - 009 - " + LONG + ".msg"),
        # A name the archive cut short to fit the path ends in an ellipsis and is still recognised.
        ("009 - cibo de pago de Tesla del mes de ma....msg", LONG, "009 - " + LONG + ".msg"),
        # An archive written when only ASCII was kept in a name.
        ("004 - visi_n anual.msg", "Revisión anual", "004 - Revisión anual.msg"),
        # Case differs, name is still cut.
        ("004 - VISIÓN ANUAL.msg", "Revisión anual", "004 - Revisión anual.msg"),
    ],
)
def test_a_long_cut_or_shortened_or_legacy_name_is_found(filename: str, subject: str, expected: str) -> None:
    assert cut_name(filename, subject) == expected


@pytest.mark.parametrize(
    ("filename", "subject"),
    [
        # Same first 24 characters as the cut form, a different ending: not this mail's cut name.
        ("009 - cibo de pago de Tesla de otro mes.msg", LONG),
        # The ellipsis form of the whole subject is a correct name, not a cut one.
        ("009 - Recibo de pago de Tesla del mes de ma....msg", LONG),
        # A cut head that also begins the whole subject cannot be told apart, so it is left alone.
        ("003 - re....msg", "Rere ere"),
    ],
)
def test_a_name_that_only_looks_alike_is_left_alone(filename: str, subject: str) -> None:
    assert cut_name(filename, subject) is None


def test_the_new_name_is_cut_to_fit_the_path_like_the_archive_does() -> None:
    folder = "C:/" + "f" * 200
    name = cut_name("009 - cibo de pago de Tesla del mes de marzo de dos mil veintiseis.msg", LONG, 255, folder)

    assert name is not None and name.startswith("009 - Recibo de pago") and name.endswith("....msg")
    assert len(folder) + 1 + len(name) <= 255


def _facts(subjects: dict[str, str]) -> Any:
    def read(path: str, _preview: int) -> dict[str, Any]:
        name = Path(path).name
        if name not in subjects:
            raise ReadError("parse", "not a readable mail")
        return {"subject": subjects[name]}
    return read


def _manifest(*files: tuple[str, int, bool]) -> Manifest:
    return Manifest("archive", [MsgFile("archive/" + n, size, 0, online) for n, size, online in files])


def test_the_audit_lists_only_the_cut_names_and_renames_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "archive"
    folder.mkdir()
    names = {"001 - cibo de pago.msg": "Recibo de pago", "002 - Plan.msg": "Plan"}
    for name in names:
        (folder / name).write_bytes(b"x")
    files = tuple((name, 10, False) for name in names)
    manifest = Manifest(str(folder), [MsgFile(str(folder / n), 10, 0, False) for n, _, _ in files])

    result = audit([manifest], MB, _facts(names))

    assert [c.name for c in result.cut] == ["001 - cibo de pago.msg"]
    assert result.cut[0].should_be == "001 - Recibo de pago.msg"
    assert result.counts()["checked"] == 2
    assert sorted(p.name for p in folder.iterdir()) == sorted(names)


def test_an_online_only_file_over_the_budget_and_an_unreadable_one_are_not_counted_as_clean() -> None:
    manifest = _manifest(("001 - a.msg", 2 * MB, True), ("002 - b.msg", 10, False), ("003 - c.msg", 10, False))

    result = audit([manifest], MB, _facts({"002 - b.msg": "b"}))

    assert result.counts() == {"files": 3, "checked": 1, "cut": 0, "not_checked_waiting": 1,
                               "not_checked_unreadable": 1}


def test_describe_prints_the_counts_then_a_limited_list() -> None:
    manifest = _manifest(*[("%03d - cibo.msg" % i, 10, False) for i in range(1, 4)])
    result = audit([manifest], MB, _facts({"%03d - cibo.msg" % i: "Recibo" for i in range(1, 4)}))

    lines = describe(result, 2)

    assert lines[0].startswith("3 archived mail(s) listed, 3 read, 3 with a cut name")
    assert len(lines) == 4 and lines[-1] == "  ... and 1 more"
