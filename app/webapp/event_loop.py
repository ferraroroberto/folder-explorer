"""The selector event loop for every uvicorn start on Windows.

asyncio's default Windows proactor loop closes its listening socket the first
time ``accept()`` raises, and a phone dropping a connection mid-handshake
raises exactly that: the process stays up and stops answering. The selector
loop has no such failure mode. Every start passes ``--loop``/``loop=`` with
:data:`LOOP_FACTORY`; uvicorn calls the factory with no arguments and needs an
instantiated loop back.
"""

from __future__ import annotations

import asyncio
import sys

LOOP_FACTORY = "app.webapp.event_loop:selector_loop_factory"


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()
