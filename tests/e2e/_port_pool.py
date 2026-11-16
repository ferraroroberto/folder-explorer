"""Name a loopback port-pool failure in the gate output.

Every closed loopback connection leaves an (ephemeral port, server port) pair in TIME_WAIT for
minutes, and Windows answers a ``connect()`` that lands on one with ``net::ERR_ADDRESS_IN_USE``
instead of trying another port. So a page that is fine fails when the host has run low, and a second
gate on the same machine makes it likelier. This module makes that failure read as what it is:

* ``pytest_runtest_makereport`` puts a ``PORT POOL EXHAUSTED`` headline ahead of a failure whose text
  carries one of the markers.
* ``pytest_terminal_summary`` ends the run with a section of that name listing the tests.

Both hooks are re-exported by ``conftest.py``, where pytest looks for them. The section is read from
the reports the terminal reporter already holds, so it also prints when the cases run in workers.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

# The browser's page load, and Playwright's Node-side request context (`connect ENOBUFS 127.0.0.1:<port>`).
PORT_POOL_MARKERS = ("ERR_ADDRESS_IN_USE", "connect ENOBUFS")
PORT_POOL_HEADLINE = (
    "PORT POOL EXHAUSTED - the host ran out of loopback ephemeral ports or socket buffers "
    "(ERR_ADDRESS_IN_USE / connect ENOBUFS); this is not a page failure. Another gate or a recent run "
    "holds ports in TIME_WAIT: wait a few minutes and re-run instead of debugging the page."
)


def is_port_pool_failure(text: str) -> bool:
    return any(marker in text for marker in PORT_POOL_MARKERS)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Relabel a report that failed on a port-pool marker so its first line names the host."""
    report = yield
    if report.failed and is_port_pool_failure(report.longreprtext) and PORT_POOL_HEADLINE not in report.longreprtext:
        report.longrepr = f"{PORT_POOL_HEADLINE}\n\n{report.longreprtext}"
    return report


def port_pool_nodeids(reports: list[Any]) -> list[str]:
    """The node ids of the failed or errored reports that carry the headline, once each."""
    hit = [r.nodeid for r in reports if PORT_POOL_HEADLINE in r.longreprtext]
    return list(dict.fromkeys(hit))


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """List the tests that failed on the port pool in a section of their own."""
    stats = terminalreporter.stats
    hit = port_pool_nodeids([*stats.get("failed", []), *stats.get("error", [])])
    if not hit:
        return
    terminalreporter.write_sep("=", "PORT POOL EXHAUSTED", red=True, bold=True)
    terminalreporter.write_line(PORT_POOL_HEADLINE)
    for nodeid in hit:
        terminalreporter.write_line(f"  {nodeid}")
