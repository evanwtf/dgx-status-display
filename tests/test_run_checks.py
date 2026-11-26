"""scripts/run_checks.py: run every step, fail if any step failed or skipped."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from run_checks import NO_SKIP, STEPS, UV, run_all


class FakeRunner:
    """Records each argv; fails the steps whose first word is in `fail`."""

    def __init__(
        self, fail: frozenset[str] = frozenset(), out: str = "1 passed"
    ) -> None:
        self.fail = fail
        self.out = out
        self.ran: list[list[str]] = []

    def __call__(self, argv: Sequence[str], capture: bool) -> tuple[int, str | None]:
        self.ran.append(list(argv))
        rc = 1 if argv[len(UV)] in self.fail else 0
        return rc, (self.out if capture else None)


def all_steps() -> list[list[str]]:
    return [[*UV, *s] for s in (*STEPS, NO_SKIP)]


def test_steps_match_ci() -> None:
    assert [list(s) for s in STEPS] == [
        ["ruff", "format", "--check", "."],
        ["ruff", "check", "."],
        ["mypy"],
        ["pytest", "-q", "-rs"],
    ]
    assert list(NO_SKIP) == [
        "pytest",
        "-q",
        "-rs",
        "-m",
        "not integration and not slow",
    ]
    assert UV == ("uv", "run", "--frozen")


def test_all_pass() -> None:
    runner = FakeRunner()
    assert run_all(STEPS, NO_SKIP, runner) == 0
    assert runner.ran == all_steps()


@pytest.mark.parametrize("failing", ["ruff", "mypy", "pytest"])
def test_a_failing_step_fails_the_run_and_every_step_still_runs(
    failing: str, caplog: pytest.LogCaptureFixture
) -> None:
    runner = FakeRunner(fail=frozenset({failing}))
    assert run_all(STEPS, NO_SKIP, runner) == 1
    assert runner.ran == all_steps()
    assert "failed:" in caplog.text


def test_a_skip_fails_the_run() -> None:
    runner = FakeRunner(out="1 passed, 1 skipped\nSKIPPED [1] tests/x.py: no GPU")
    assert run_all(STEPS, NO_SKIP, runner) == 1
    assert runner.ran == all_steps()
