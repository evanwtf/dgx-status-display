# dgx-status-display: agent guide

A status screen for a DGX Spark. A Python daemon (`dgx-status`) on the Spark
sends one JSON frame per second over USB serial to a Waveshare
ESP32-C6-Touch-AMOLED-2.16. ESP-IDF firmware draws it with LVGL.

`CLAUDE.md` and `GEMINI.md` are symlinks to this file. Edit `AGENTS.md` only.

## Baseline policies

* Make minimal, focused changes; avoid broad refactors unless requested.
* Preserve existing architecture and patterns.
* Don't introduce new dependencies without justification.
* Update tests when behavior changes; update docs when user-visible
  behavior, configuration, or workflows change.

## Hardware safety

- **Do not flash the board, open its serial port, or run esptool against it
  unless a person asks for that step.** esptool resets the chip, even for a
  read. A person backs up the factory firmware before the first flash
  (README, step 3).
- Read live data only: `/proc`, `/sys` and NVML. `--dry-run` opens no device.
- No system changes from agent work: no apt, `usermod`, udev rules or
  systemd install.

## Layout

| Path | Holds |
| --- | --- |
| `src/dgx_status/` | The daemon. Parsers (`procfs.py`, `sysfs.py`, `occupant.py`), NVML (`gpu.py`), frame encoder (`frame.py`), `collector.py`, `serial_link.py`, `daemon.py`, `cli.py`. |
| `tests/` | One file per module. `fixtures/spark/` holds /proc and /sys files copied from a DGX. No hook may rewrite them. |
| `firmware/` | ESP-IDF project. `main/logic/` is pure C with host tests in `firmware/test/`. `main/board.h` cites the pin sources. |
| `scripts/` | `firmware.py` (build and test in the pinned IDF image), `capture_fixtures.py`. |
| `deploy/` | The systemd unit. |
| `docs/` | `protocol.md` (the wire format), `plan.md`, `changelog.md`. |

## Commands

Run these before you push. CI runs the same steps.

```bash
uv sync
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest -q -rs
uv run python scripts/firmware.py test     # C unit tests, ASan + UBSan
uv run python scripts/firmware.py build    # idf.py build, zero warnings in main/
```

## Rules for this repo

- **Protocol first.** A change to the frame changes `docs/protocol.md`, the
  Python encoder and the C parser in one commit, with tests on both sides.
- **Readings are instantaneous.** No averages, ranges or rolling windows.
  CPU % is the /proc/stat delta between two frames.
- **Memory is MemTotal − MemAvailable.** Never MemFree. NVML memory is N/A on
  GB10.
- **No SM clock.** Evan dropped it as a useless indicator.
- **Find hwmon devices by `name`**, never by the `hwmonN` number.
- **Unknown is a dash, not a zero.** A missing reading is left out of the
  frame and drawn as `—`. `idle` on the GPU line is a real state; NVML
  failure is unknown.
- **One image pin.** `scripts/firmware.py` holds `IDF_IMAGE`; the README
  repeats it and a test keeps them equal. Component versions are exact in
  `firmware/main/idf_component.yml` and locked in `firmware/dependencies.lock`.
- **The console stays on UART0.** USB-Serial-JTAG carries frames only. Never
  log to it.

## Delivery

Push to `main` until the repository has real users or shared agent files.
After that, open one batched pull request per session.

## Secrets

Phase 1 has no secrets. Phase 2 adds a Home Assistant token. It lives in a
password manager and is read at run time. Never commit it, and never name
its vault or item here. `.env.example` lists every variable with no real
value.

## Public repository

This repository is public. No tracked file may name a host, a private DNS
name, a LAN address (`10.x` is allowed), a home-directory path, a board's
serial number or MAC, or a secret's storage location. Describe a machine by
its role. `tests/test_public_guard.py` enforces this and reports `file:line`
only.

## Pull requests from forks

- **Never approve a fork pull request's workflow run.** The jobs run on
  self-hosted runners; an approved run executes the fork's code on a machine
  on Evan's desk. The `fork-guard` job fails such a PR on purpose.
- **Never approve a run for any PR that touches `.github/`**, from a fork or
  not, without Evan's explicit go-ahead.
- To test a fork's change, a maintainer reads it, then pushes it to a branch
  in this repo.

## Before doing this, read

| Before doing this | Rule | Read |
| --- | --- | --- |
| Changing Python tooling | uv, `src/` layout, strict mypy, `logging` to stdout and never `print`. | `~/git/baseline/conventions/python.md` ([GitHub](https://github.com/evanwtf/baseline/blob/main/conventions/python.md)) |
| Adding or changing tests | Write the failing test first where a bug is expensive. A skipped test is not a passing test. | `~/git/baseline/conventions/testing.md` ([GitHub](https://github.com/evanwtf/baseline/blob/main/conventions/testing.md)) |
| Changing CI | `--frozen` on every uv step; a per-run uv cache; fail on a skip; artifacts 3 days at most. | `~/git/baseline/conventions/ci.md` ([GitHub](https://github.com/evanwtf/baseline/blob/main/conventions/ci.md)) |
| Committing or opening a PR | Subject `area: imperative summary`; no agent session details. | `~/git/baseline/conventions/git-and-prs.md` ([GitHub](https://github.com/evanwtf/baseline/blob/main/conventions/git-and-prs.md)) |
| Adding or reading a secret | 1Password; never a value on disk alone. | `~/git/baseline/conventions/secrets.md` ([GitHub](https://github.com/evanwtf/baseline/blob/main/conventions/secrets.md)) |
| Writing docs | Short sentences, active voice, US spelling. | `~/git/baseline/conventions/writing.md` ([GitHub](https://github.com/evanwtf/baseline/blob/main/conventions/writing.md)) |
