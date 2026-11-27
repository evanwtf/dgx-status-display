# Changelog

## 0.0.4 (2026-11-27)

Far mode: one metric per page, in text large enough to read from 2 m.
The board starts in far mode; a short press of BOOT switches to the tiles.

Upgrade: if you build from source, delete `firmware/sdkconfig` before you
build. A sdkconfig from an older build keeps the old LVGL settings, and
far mode then draws no text; the build now stops with "stale
firmware/sdkconfig" instead. To keep the stored settings, flash the zip's
parts with `@flash_args`; `flash-merged` erases them, the display mode
included.

- Far mode, the new default: one metric per page (GPU, GPU PWR, GPU TEMP,
  LLM, MODEL, MEMORY, CPU), sized from an eye chart read on the board at
  2 m. Words have 100 px capitals (81 px for GPU PWR, GPU TEMP and MEMORY,
  which are too wide), values 141 px digits (109 px for 4 or more
  characters), units 81 px capitals, and names 100 px capitals when they
  fit, else 81 px. The metric's level fill, or its 2-minute history, spans
  the screen. LLM and MODEL show only while something runs.
- BOOT: a short press switches between far and near mode, with a fade, and
  NVS keeps the choice. Near mode (the 2×2 tiles) is unchanged. The board
  logs `mode far` or `mode near`.
- Firmware: the LVGL heap is 96 KB (was 48 KB). A glyph of the 141 px
  digits needs a 16 KB buffer on it while it draws. The far-mode fonts are
  RLE-compressed (`LV_USE_FONT_COMPRESSED`).
- Firmware: a failed LVGL allocation traps (core dump, reboot) instead of
  drawing a blank glyph. The minute `free:` line reports the LVGL heap
  peak. A page redraws only what changed.
- Daemon: a vLLM engine on the GPU shows as `vllm`. vLLM renames its
  processes (`VLLM::EngineCore`, `VLLM::Worker_TP0`), so the Running tile
  showed the cut process title.
- README: the far-mode section shows the eight far pages, rendered by the
  preview. `make readme-images` refreshes them and the near-mode image.

## 0.0.3 (2026-11-26)

- A top-level `Makefile`: `make` lists the targets. Each target calls `uv`
  or a script in `scripts/`; none repeats the image pin. The README starts
  with the requirements and a quick start with make.
- Board steps in `scripts/firmware.py`: `flash` (with `flash_args`; keeps
  the settings), `flash-merged` (the merged image at 0x0; erases the
  settings), `backup` (the whole 16 MB flash, verified, with a `.sha256`)
  and `monitor`. Each runs esptool in the pinned image with `--device`.
- Refusals: the board steps refuse a port that is not one `/dev/ttyACM*` or
  `/dev/ttyUSB*` device, and refuse while the service runs. `flash` and
  `flash-merged` refuse without a factory backup that matches its
  `.sha256` (`NO_BACKUP=1` overrides). `backup` never replaces a backup.
- `make install-service` renders the unit with `scripts/service.py`. It
  refuses root, an unsafe checkout path, and a unit for another checkout
  (`FORCE=1` overrides). It restarts the service, so a new unit takes
  effect.
- CI's python job runs `make check` (`scripts/run_checks.py`).

## 0.0.2 (2026-11-26)

New firmware needs the daemon from this version. With an older daemon,
the Network and Storage pages show dashes and the LLM page stays hidden.
Install the new systemd unit too: the old one hides the host network
from the daemon (`PrivateNetwork=yes`), so the LAN and Cluster IP tiles
show dashes and the LLM page stays hidden.

- Page changes fade through black: the panel brightness falls to 0 in
  150 ms on a quadratic curve, the new view is drawn whole at 0, and the
  level rises again in 150 ms. The 10 s dwell starts after the fade-in.
  Taps, the detail page and a KEY rotation fade too; NO DATA does not. The
  `free:` line reports the last transition's time.
- LLM page: Output and Prompt tok/s, Requests running and KV cache %, from
  the local LLM metrics exporter, in a slate family. The carousel shows it
  only while an engine is up. It hides it while the exporter is down or
  slower than 15 s. The exporter must listen on loopback.
  The detail page opened from it lists the models, the time to first token
  and the draft-token acceptance.
- Network and Storage pages: LAN in/out against the link speed, RoCE in/out
  and Cluster IP against the ConnectX-7's 200 Gb/s, SSD read/write against
  14 GB/s, and disk use in TB. Each rate is a counter delta between two
  frames. Memory's total is now the 128 GiB installed, not MemTotal.

## 0.0.1 (2026-11-26)

The first release. It is phase 1: a DGX Spark sends its status over USB
serial, and a Waveshare ESP32-C6-Touch-AMOLED-2.16 shows it. There is no
Wi-Fi yet. The release holds the daemon (a wheel and an sdist), the
systemd unit template and the firmware: one merged image to flash at 0x0,
the separate images with `flash_args`, and the ELF to decode a core dump.
`SHA256SUMS` covers every file. The daemon installs from a clone of the
tag, with its locked dependencies.

Flash the merged image only for a first install: it erases the stored
settings (the screen rotation and the chart style). To upgrade, flash the
zip's parts with `write_flash @flash_args`, which keeps them.

- Releases: a `v*` tag publishes a GitHub release. The workflow refuses a
  tag that differs from any declared version, and takes the notes from this
  file (`scripts/release_check.py`).

After the first board test:

- Buttons: the firmware logs each press of BOOT (GPIO9), KEY (GPIO10) and
  PWR (GPIO18 and the PMU's key IRQs), so the daemon relays them. Holding
  PWR no longer switches the board off: the firmware clears the PMU's key
  power-off bit and sets its hold time to the 10 s maximum. To switch the
  board off, unplug it.
- Rotation: a short KEY press turns the screen 90° clockwise, stored in NVS;
  touch follows. Software rotation (the panel cannot rotate) costs one
  38,400 B DMA buffer; without room for it the board starts with a fixed
  screen. The once-a-minute `free:` line reports the angle and
  re-reads the PMU's key power-off bit.
- Design: Running, Up and Load use moss green, apart from the warm
  temperature amber. The top strip sits lower and further in, clear of the
  panel's rounded corners at every burn-in offset; the preview checks it.
- Design pass: colored tiles whose level fill (or, as an option, a 2-minute
  history chart) shows the reading; temperature tiles change hue at the
  thresholds; Barlow Semi Condensed for words; a WCAG contrast test for
  every text color over every shade; the four tiles' outer corners follow
  the panel's corners.
- Board logs on USB on purpose, relayed by the daemon; a core dump in flash
  and `scripts/firmware.py coredump`; the reset reason in the hello.
- Fix: the task watchdog subscribed the main task and rebooted the board
  every 10 s.
- Firmware: a carousel of three 2×2 tile pages (GPU, Cooling, Host) with big
  values, a top strip (link dot, clock, page dots), tap for the next page,
  long press for every temperature. NO DATA stops the carousel.
- Values use JetBrains Mono SemiBold, generated by `scripts/gen_fonts.py`,
  right-aligned with fixed decimals so digits keep their columns. Fans show
  kRPM. One table holds the color thresholds.
- `scripts/firmware.py preview` renders the real UI code on the host to PNGs
  and checks the digit columns.
- Firmware hardening from review: a JSON depth limit before cJSON, the
  AXP2101 is written only after its chip ID matches, a task watchdog on the
  UI, serial RX below the UI's priority, rate-limited reject logs, a wider
  burn-in orbit and an idle dim.
- Daemon hardening: safe decoding of `/proc` names, no `tcdrain` on hang-up,
  NVML re-init after repeated failure.

First cut:

- Protocol v1: one compact JSON line per second, at most 1024 bytes. Only
  `v`, `t` and `host` are required; a missing reading shows as a dash.
- Daemon `dgx-status`: GPU util, power and temperature from NVML; the GPU
  occupant from NVML compute processes and /proc; memory as MemTotal −
  MemAvailable; CPU % from consecutive /proc/stat samples; load, uptime;
  fans from the `nvfanread` hwmon device; board, NVMe and NIC temperatures.
  No SM clock. It reopens the serial port with backoff after an unplug.
- Firmware for the Waveshare ESP32-C6-Touch-AMOLED-2.16: overview and
  detail pages, LIVE / STALE / NO DATA, burn-in care (pixel shift, gray text,
  dimming), a hello line with the firmware version. Pins and panel init come
  from Waveshare's demo code.
- The daemon finds the board's `/dev/serial/by-id/` port itself;
  `DGX_STATUS_DEVICE` takes a path or a glob.
- systemd unit as a template (`@USER@`, `@REPO@`) with sandboxing, and CI on
  the self-hosted runner, with a fork guard.
- MIT license; NOTICE credits the Waveshare hardware facts. A test keeps
  private infrastructure out of every file.
