"""Outlook's "no date" value never reaches the sheet or a file name.

An unsent item (a draft open in a compose window) reports ``SentOn`` and
``ReceivedTime`` as 4501-01-01, Outlook's none-date. Read as a real date it
showed "1 Jan 4501" in the Archive sheet and would prefix a filed file name.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.outlook.client import sent_datetime

NONE_DATE = datetime(4501, 1, 1)


def _item(sent: datetime | None, received: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(SentOn=sent, ReceivedTime=received)


def test_an_unsent_item_has_no_sent_date() -> None:
    assert sent_datetime(_item(NONE_DATE, NONE_DATE)) is None


def test_the_none_date_falls_through_to_a_real_received_time() -> None:
    received = datetime(2026, 11, 5, 9, 12, 0)
    assert sent_datetime(_item(NONE_DATE, received)) == received


def test_a_real_sent_date_is_kept() -> None:
    sent = datetime(2026, 11, 5, 9, 12, 30)
    assert sent_datetime(_item(sent, NONE_DATE)) == sent
