"""The board's log lines reach our log at the right level, rate-limited."""

from __future__ import annotations

import logging

import pytest

from dgx_status.board_log import BoardLogRelay, clean


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _records(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


@pytest.fixture
def relay() -> tuple[BoardLogRelay, Clock]:
    clock = Clock()
    return BoardLogRelay(clock, window_s=60), clock


def test_error_and_warning_lines_are_warnings(
    relay: tuple[BoardLogRelay, Clock], caplog: pytest.LogCaptureFixture
) -> None:
    r, _ = relay
    with caplog.at_level(logging.DEBUG, logger="dgx_status.board_log"):
        r.relay(b"E (3720) task_wdt: esp_task_wdt_reset(707): task not found")
        r.relay(
            b"W (99) serial_rx: rejected 3 line(s) in the last 10 s; latest: bad json"
        )
    assert _records(caplog, logging.WARNING) == [
        "board: E task_wdt: esp_task_wdt_reset(707): task not found",
        "board: W serial_rx: rejected 3 line(s) in the last 10 s; latest: bad json",
    ]


def test_info_lines_and_banner_text_are_debug(
    relay: tuple[BoardLogRelay, Clock], caplog: pytest.LogCaptureFixture
) -> None:
    r, _ = relay
    with caplog.at_level(logging.DEBUG, logger="dgx_status.board_log"):
        r.relay(b"I (461) cpu_start: GPIO 17 and 16 are used as console UART I/O pins")
        r.relay(b"ESP-ROM:esp32c6-20220919")
        r.relay(b"load:0x40875720,len:0x1804")
        r.relay(b"")
    assert _records(caplog, logging.WARNING) == []
    assert len(_records(caplog, logging.DEBUG)) == 3


def test_every_reboot_banner_is_a_warning(
    relay: tuple[BoardLogRelay, Clock], caplog: pytest.LogCaptureFixture
) -> None:
    r, _ = relay
    with caplog.at_level(logging.INFO, logger="dgx_status.board_log"):
        for _ in range(3):
            r.relay(b"rst:0xc (SW_CPU),boot:0x1f (SPI_FAST_FLASH_BOOT)")
    assert (
        _records(caplog, logging.WARNING)
        == ["board reset: rst:0xc (SW_CPU),boot:0x1f (SPI_FAST_FLASH_BOOT)"] * 3
    )


def test_repeats_are_held_back_for_the_window_then_counted(
    relay: tuple[BoardLogRelay, Clock], caplog: pytest.LogCaptureFixture
) -> None:
    r, clock = relay
    line = b"E (%d) task_wdt: esp_task_wdt_reset(707): task not found"
    with caplog.at_level(logging.INFO, logger="dgx_status.board_log"):
        for i in range(240):  # every 250 ms for 60 s; the uptime differs
            r.relay(line % (3720 + 250 * i))
            clock.now += 0.25
        r.relay(line % 99999)  # 60 s after the first: logged again, with a count
    warnings = _records(caplog, logging.WARNING)
    assert warnings == [
        "board: E task_wdt: esp_task_wdt_reset(707): task not found",
        "board: E task_wdt: esp_task_wdt_reset(707): task not found"
        " (and 239 more in the last 60 s)",
    ]


def test_distinct_messages_are_limited_separately(
    relay: tuple[BoardLogRelay, Clock], caplog: pytest.LogCaptureFixture
) -> None:
    r, _ = relay
    with caplog.at_level(logging.INFO, logger="dgx_status.board_log"):
        r.relay(b"E (1) a: one")
        r.relay(b"E (2) a: two")
        r.relay(b"E (3) a: one")
        r.relay(b"W (4) a: one")
    assert len(_records(caplog, logging.WARNING)) == 3


def test_clean_strips_colors_and_control_bytes() -> None:
    assert clean(b"\x1b[0;31mE (1) x: bad\x07 \xff\x1b[0m\r") == "E (1) x: bad  \ufffd"
