"""``scripts\\install.bat`` / ``python -m app.install``: set this checkout up on a PC, or bring a set-up one in step, keeping its settings.

Each step reports one line: what it created or added, or ``unchanged`` / ``kept`` when there was nothing to do, so a
second run reports only those and changes nothing. ``-WhatIf`` lists every change and makes none. The run ends with
a checklist (src/install/checklist.py) of what works and what still needs the owner. Steps, in order:

1. ``.venv``: created with the Python running this when missing, kept when present.
2. ``config.json`` and ``.env`` (src/install/config_merge.py): created from their tracked templates when missing;
   an existing config only gains the keys it lacks, after a timestamped backup; nothing is ever overwritten.
3. Requirements: ``requirements.txt`` and ``requirements-tray.txt``, plus the email and Outlook files when their
   feature block is on in the config, through the venv's pip.
4. ``--startup`` only: a Startup-folder shortcut to ``tray.bat`` (src/install/startup.py), kept when one exists.

``--uninstall`` removes only that shortcut, and only when it is this installer's and points at this checkout.

Standard library only: it runs with the system Python before ``.venv`` exists. Docs: docs/install.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.install import checklist, config_merge, startup
from src.no_window import NO_WINDOW

ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)
# Always installed: the web app, and the tray that tray.bat starts.
BASE_REQUIREMENTS = ("requirements.txt", "requirements-tray.txt")
# Installed when the feature block is on (src/features.py): a machine that switched one off skips its packages.
FEATURE_REQUIREMENTS = {"email": "requirements-email.txt", "archive": "requirements-outlook.txt"}
_VENV_TIMEOUT_S = 300
_PIP_TIMEOUT_S = 1800

logger = logging.getLogger("install")


@dataclass
class Step:
    """One reported line: ``changed`` counts toward the result, ``failed`` makes the run exit 1."""

    name: str
    status: str
    detail: str = ""
    changed: bool = False
    failed: bool = False


def venv_python(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "python.exe"


def ensure_venv(root: Path, *, what_if: bool) -> Step:
    py = venv_python(root)
    if py.is_file():
        return Step(".venv", config_merge.KEPT)
    if what_if:
        return Step(".venv", "would create", changed=True)
    done = subprocess.run([sys.executable, "-m", "venv", str(root / ".venv")], capture_output=True, text=True,
                          timeout=_VENV_TIMEOUT_S, check=False, creationflags=NO_WINDOW)
    if done.returncode != 0 or not py.is_file():
        return Step(".venv", "failed", done.stderr.strip()[-400:], failed=True)
    return Step(".venv", config_merge.CREATED, f"with Python {sys.version.split()[0]}", changed=True)


def requirement_files(root: Path, cfg: dict[str, Any] | None) -> list[str]:
    features = (cfg or {}).get("features")
    chosen = features if isinstance(features, dict) else {}
    files = list(BASE_REQUIREMENTS)
    files += [name for key, name in FEATURE_REQUIREMENTS.items() if chosen.get(key, True) is not False]
    return [name for name in files if (root / name).is_file()]


def install_requirements(root: Path, files: list[str], *, what_if: bool) -> Step:
    """pip install the files; ``unchanged`` when every requirement was already met."""
    py = venv_python(root)
    if not py.is_file():
        return Step("requirements", "would install" if what_if else "skipped", "after .venv is created",
                    changed=what_if, failed=not what_if)
    argv = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
    if what_if:
        argv.append("--dry-run")
    for name in files:
        argv += ["-r", name]
    done = subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=_PIP_TIMEOUT_S, check=False, creationflags=NO_WINDOW)
    listed = ", ".join(files)
    if done.returncode != 0:
        return Step("requirements", "failed", (done.stderr or done.stdout).strip()[-600:], failed=True)
    marker = "Would install" if what_if else "Successfully installed"
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip().startswith(marker)]
    if not lines:
        return Step("requirements", config_merge.UNCHANGED, listed)
    return Step("requirements", "would install" if what_if else "installed", lines[-1][len(marker):].strip(),
                changed=True)


def file_step(outcome: config_merge.FileOutcome, *, what_if: bool) -> Step:
    status = f"would be {outcome.status}" if what_if and outcome.changed else outcome.status
    detail = outcome.detail
    if outcome.added:
        detail = ", ".join(outcome.added) + (f"; backup {outcome.backup.name}" if outcome.backup else "")
    return Step(outcome.name, status, detail, changed=outcome.changed,
                failed=outcome.status in (config_merge.UNREADABLE, config_merge.NO_TEMPLATE))


def startup_step(root: Path, env: Mapping[str, str], *, what_if: bool) -> Step:
    folder = startup.startup_dir(env)
    if folder is None:
        return Step("start at sign-in", "not confirmed", "APPDATA is not set", failed=True)
    present = startup.find_entries(folder, root)
    if present:
        return Step("start at sign-in", config_merge.KEPT, present[0].name)
    if what_if:
        return Step("start at sign-in", "would create", startup.SHORTCUT_NAME, changed=True)
    try:
        link = startup.create_shortcut(folder, root)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return Step("start at sign-in", "failed", str(exc), failed=True)
    return Step("start at sign-in", config_merge.CREATED, link.name, changed=True)


def effective_config(root: Path, env: Mapping[str, str]) -> dict[str, Any] | None:
    """The config as the run leaves it (under ``-WhatIf``, as it would leave it); None when it does not parse."""
    target = config_merge.config_target(root, env)
    template_path = root / config_merge.CONFIG_TEMPLATE
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
        if not target.exists():
            return template if isinstance(template, dict) else None
        existing = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(existing, dict) or not isinstance(template, dict):
        return None
    return config_merge.merged(existing, template)


def install(root: Path, env: Mapping[str, str], *, what_if: bool, with_startup: bool,
            with_deps: bool = True) -> list[Step]:
    """Run every step; each one is idempotent, and under ``what_if`` none writes anything."""
    steps = [ensure_venv(root, what_if=what_if)] if with_deps else []
    steps.append(file_step(config_merge.ensure_config(root, env, what_if=what_if), what_if=what_if))
    steps.append(file_step(config_merge.ensure_env(root, what_if=what_if), what_if=what_if))
    if with_deps:
        steps.append(install_requirements(root, requirement_files(root, effective_config(root, env)),
                                          what_if=what_if))
    if with_startup:
        steps.append(startup_step(root, env, what_if=what_if))
    return steps


def uninstall(root: Path, env: Mapping[str, str], *, what_if: bool) -> list[Step]:
    folder = startup.startup_dir(env)
    if folder is None:
        return [Step("start at sign-in", "not confirmed", "APPDATA is not set", failed=True)]
    link = startup.removable(folder, root)
    if link is None:
        return [Step("start at sign-in", config_merge.UNCHANGED, "no shortcut of this installer's points here")]
    if not what_if:
        link.unlink()
    return [Step("start at sign-in", "would remove" if what_if else "removed", link.name, changed=True)]


def report(steps: list[Step], checks: list[checklist.Check], *, what_if: bool) -> int:
    for step in steps:
        mark = "FAIL" if step.failed else ("change" if step.changed else "ok")
        logger.info("[%s] %s: %s%s", mark, step.name, step.status, f" ({step.detail})" if step.detail else "")
    changes = sum(step.changed for step in steps)
    if what_if:
        logger.info("-WhatIf: %d change(s) would be made; nothing was changed.", changes)
    else:
        logger.info("Result: %s.", "unchanged" if changes == 0 else f"{changes} change(s) made")
    if checks:
        logger.info("")
        logger.info("Checklist:")
        for check in checks:
            logger.info("  [%s] %s: %s", check.state, check.name, check.detail)
    return 1 if any(step.failed for step in steps) else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(prog="scripts\\install.bat", description=__doc__.splitlines()[0])
    parser.add_argument("-WhatIf", "--whatif", dest="what_if", action="store_true",
                        help="list every change and make none")
    parser.add_argument("--startup", action="store_true", help="also start the tray at sign-in (Startup folder)")
    parser.add_argument("--uninstall", action="store_true", help="remove only the Startup shortcut this installer made")
    args = parser.parse_args(argv)
    if sys.version_info < MIN_PYTHON:
        logger.error("❌ Python %s or newer is needed; this is %s.", ".".join(map(str, MIN_PYTHON)), sys.version.split()[0])
        return 1
    env = dict(os.environ)
    if args.uninstall:
        return report(uninstall(ROOT, env, what_if=args.what_if), [], what_if=args.what_if)
    steps = install(ROOT, env, what_if=args.what_if, with_startup=args.startup)
    deps = next((s for s in steps if s.name == "requirements"), None)
    deps_state = checklist.UNKNOWN if deps is None or args.what_if else (
        checklist.MISSING if deps.failed else checklist.OK)
    checks = checklist.build(ROOT, env, effective_config(ROOT, env), venv_ok=venv_python(ROOT).is_file(),
                             deps_state=deps_state, deps_detail=deps.status if deps else "not run")
    return report(steps, checks, what_if=args.what_if)


if __name__ == "__main__":
    raise SystemExit(main())
