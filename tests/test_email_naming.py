"""The subject rule every email path shares: a reply prefix needs its colon, a word that starts like one stays whole."""

from __future__ import annotations

import pytest

from src.email.naming import clean_subject


@pytest.mark.parametrize(
    "subject",
    [
        "Recibo de pago de Tesla",
        "Reserva confirmada",
        "Revisión anual",
        "Renovación de contrato",
        "Reunión del lunes",
        "Report for March",
        "Review of the draft",
        "Rvalue notes",
        "Fwd the plan",
        "Fwtool release",
    ],
)
def test_a_word_that_starts_like_a_prefix_is_left_whole(subject: str) -> None:
    assert clean_subject(subject) == subject


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("RE: Recibo de pago", "Recibo de pago"),
        ("Rv: x", "x"),
        ("FW: x", "x"),
        ("Fw: x", "x"),
        ("Fwd: Re: x", "x"),
        ("RE: RV: Fwd: x", "x"),
        ("  re : spaced", "spaced"),
        ("RE： full-width", "full-width"),
        ("Fwd： Re： chained", "chained"),
        ("RE:no space", "no space"),
        ("Re: Revisión anual", "Revisión anual"),
    ],
)
def test_a_real_prefix_is_stripped(subject: str, expected: str) -> None:
    assert clean_subject(subject) == expected


def test_a_subject_that_is_only_a_prefix_becomes_empty() -> None:
    assert clean_subject("RE:") == ""
    assert clean_subject(None) == ""


def test_the_msg_suffix_still_goes_when_asked() -> None:
    assert clean_subject("RE: Recibo.msg", strip_msg_suffix=True) == "Recibo"
