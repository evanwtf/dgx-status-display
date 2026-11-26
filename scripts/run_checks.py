"""Run every Python check that CI's python job runs, and report all failures.

    uv run python scripts/run_checks.py      # what `make check` runs

Each step runs even when an earlier one fails; the exit code is 1 if any
step failed. The steps:

    ruff format --check .
    ruff check .
    mypy
    pytest -q -rs                (the NVML integration test may skip)
    pytest without the hardware suites, which must skip nothing

Stdlib only.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

logger = logging.getLogger("run_checks")

REPO = Path(__file__).resolve().parent.parent
UV = ("uv", "run", "--frozen")
STEPS: tuple[tuple[str, ...], ...] = (
    ("ruff", "format", "--check", "."),
    ("ruff", "check", "."),
    ("mypy",),
    ("pytest", "-q", "-rs"),
)
NO_SKIP: tuple[str, ...] = (
    "pytest",
    "-q",
    "-rs",
    "-m",
    "not integration and not slow",
)

# A step runner returns (exit code, captured output or None when not captured).
Runner = Callable[[Sequence[str], bool], tuple[int, str | None]]


def run_step(argv: Sequence[str], capture: bool) -> tuple[int, str | None]:
    if not capture:
        return subprocess.call(list(argv), cwd=REPO), None
    out = subprocess.run(
        list(argv),
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return out.returncode, out.stdout


def run_all(
    steps: Sequence[Sequence[str]],
    no_skip: Sequence[str],
    runner: Runner = run_step,
) -> int:
    """Run each step, then the no-skip step. 1 if any failed, else 0."""
    failed: list[str] = []
    for step in steps:
        argv = [*UV, *step]
        logger.info("+ %s", " ".join(argv))
        rc, _ = runner(argv, False)
        if rc != 0:
            failed.append(" ".join(step))
    argv = [*UV, *no_skip]
    logger.info("+ %s (must skip nothing)", " ".join(argv))
    rc, out = runner(argv, True)
    if out:
        logger.info("%s", out.rstrip())
    if rc != 0:
        failed.append("pytest without the hardware suites")
    elif out is not None and "SKIPPED" in out:
        logger.error("unexpected skip")
        failed.append("pytest without the hardware suites (a test skipped)")
    for name in failed:
        logger.error("failed: %s", name)
    return 1 if failed else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    return run_all(STEPS, NO_SKIP)


if __name__ == "__main__":
    raise SystemExit(main())
