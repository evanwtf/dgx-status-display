"""A bad log level falls back to INFO instead of raising."""

from __future__ import annotations

import logging
import sys
from typing import Any

import pytest

from dgx_status import logging_setup


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: seen.append(kw))
    return seen


def test_bad_level_falls_back_to_info(
    calls: list[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    logging_setup.configure("LOUD")
    assert calls[0]["level"] == logging.INFO
    assert "unknown log level 'LOUD'; using INFO" in caplog.text


def test_good_level_is_used(calls: list[dict[str, Any]]) -> None:
    logging_setup.configure(" debug ")
    assert calls[0]["level"] == "DEBUG"
    assert calls[0]["stream"] is sys.stdout
