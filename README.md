# dgx-status-display

[![check](https://github.com/evanwtf/dgx-status-display/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/evanwtf/dgx-status-display/actions/workflows/check.yml?query=branch%3Amain)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg?logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3130/)
[![ESP-IDF v5.5.6](https://img.shields.io/badge/ESP--IDF-v5.5.6-E7352C.svg?logo=espressif&logoColor=white)](https://github.com/espressif/esp-idf/releases/tag/v5.5.6)
[![Board: Waveshare ESP32-C6-Touch-AMOLED-2.16](https://img.shields.io/badge/board-ESP32--C6--Touch--AMOLED--2.16-orange.svg)](https://docs.waveshare.com/ESP32-C6-Touch-AMOLED-2.16)

A live status screen for a DGX Spark. A Waveshare ESP32-C6-Touch-AMOLED-2.16
plugs into a USB-C port on the Spark. A small Python daemon on the Spark
sends one JSON frame per second over USB serial, and the board draws it:
GPU use, power and temperature, what is on the GPU, memory, CPU, fans and
every temperature sensor. When the frames stop, the screen says so.

Phase 1 is USB serial only. See [docs/plan.md](docs/plan.md) for phase 2
(Wi-Fi, outlet power, both Sparks) and [docs/protocol.md](docs/protocol.md)
for the frame format.

## Hardware

- **Board:** [Waveshare ESP32-C6-Touch-AMOLED-2.16](https://docs.waveshare.com/ESP32-C6-Touch-AMOLED-2.16).
  A 2.16-inch 480×480 AMOLED (CO5300 driver, QSPI), CST9220 capacitive
  touch, an AXP2101 power chip and an ESP32-C6 with 16 MB flash and no PSRAM.
- **Link:** one USB-C cable to the DGX Spark. It carries power and the
  frames (the C6's built-in USB-Serial-JTAG). No Wi-Fi in phase 1.
- **Host:** an NVIDIA DGX Spark (GB10). The fan readings need the
  `nvfanread` hwmon module.

The screen shows GPU use, power and temperature, what is on the GPU, memory,
CPU, load, fan speeds, board, NVMe and NIC temperatures, uptime and the
host's clock. A tap opens a page with every temperature sensor.

## Bootstrap

From a bare DGX Spark to a running display. Run each block in order. Docker
must work for your user (`docker ps`). The flash steps run esptool inside the
pinned ESP-IDF image with `--device`, so they need no sudo and no `dialout`
group.

### 1. Set the variables

```bash
export REPO=~/git/dgx-status-display
# The board's stable serial path. Its name holds the chip's MAC, so find it:
export PORT=$(ls /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00)
echo "$PORT"                         # must print exactly one path
export DEV=$(readlink -f "$PORT")    # for example /dev/ttyACM0
export IDF_IMAGE=espressif/idf:v5.5.6@sha256:bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84
```

### 2. Get the code and check the daemon

```bash
if [ -d "$REPO/.git" ]; then git -C "$REPO" pull --ff-only; \
  else git clone https://github.com/evanwtf/dgx-status-display "$REPO"; fi
cd "$REPO"
uv sync --frozen
uv run dgx-status --dry-run --once
```

The dry run logs one frame and opens no device. Check it: `occ` matches
`nvidia-smi`, `mem_used_gib` is MemTotal − MemAvailable, and `fan0_rpm` and
`fan1_rpm` are there (they need the `nvfanread` hwmon module).

### 3. Back up the factory firmware

Do this once, before the first flash. Each esptool command resets the chip
into its bootloader and, with `--after hard_reset` (the default), resets it
back into the factory app when it finishes. The factory screen comes back
after each step.

```bash
cd "$REPO"
mkdir -p backup
alias esptool='docker run --rm --device="$DEV" -v "$REPO:/project" -w /project "$IDF_IMAGE" esptool.py --chip esp32c6 -p "$DEV"'
esptool flash_id                      # must report a 16 MB flash
esptool get_security_info             # secure boot and flash encryption must be off
docker run --rm --device="$DEV" -v "$REPO:/project" -w /project "$IDF_IMAGE" \
  bash -c "esptool.py --chip esp32c6 -p $DEV -b 460800 read_flash 0 0x1000000 backup/factory-16MB.bin \
    && chown $(id -u):$(id -g) backup/factory-16MB.bin"
ls -l backup/factory-16MB.bin         # must be 16777216 bytes
esptool -b 460800 verify_flash 0 backup/factory-16MB.bin   # reads the chip again and compares
(cd backup && sha256sum factory-16MB.bin > factory-16MB.bin.sha256)
```

Stop if `get_security_info` shows secure boot or flash encryption enabled:
this firmware is unsigned and the flash would not boot. Git ignores
`backup/`. Copy `factory-16MB.bin` and its `.sha256` off this machine too.
Waveshare also publishes its factory image (`03_Firmware/01_Fac-v1.0.0.bin`
in [waveshareteam/ESP32-C6-Touch-AMOLED-2.16](https://github.com/waveshareteam/ESP32-C6-Touch-AMOLED-2.16)).

### 4. Build and flash the firmware

The first flash happens before the service exists, so nothing else holds the
port.

```bash
cd "$REPO"
uv run python scripts/firmware.py test     # host unit tests
uv run python scripts/firmware.py build    # idf.py build in $IDF_IMAGE
docker run --rm --device="$DEV" -v "$REPO:/project" -w /project/firmware/build "$IDF_IMAGE" \
  esptool.py --chip esp32c6 -p "$DEV" -b 460800 --before default_reset --after hard_reset \
  write_flash "@flash_args"
```

The board restarts and shows "DGX not sending".

**If esptool cannot connect** (for example, the firmware on the chip has
taken over USB): unplug the board, hold **BOOT**, plug it back in, release
BOOT. The chip now waits in its bootloader. Run the same command with
`--before no_reset` in place of `--before default_reset`. Then press the
reset button or replug.

### 5. Install the service

`deploy/dgx-status.service` is a template. The install fills in `@USER@`
(the account that runs the daemon) and `@REPO@` (the checkout). The unit
adds `dialout` for this service only. The daemon finds the board's port
itself; to pin one port, put `DGX_STATUS_DEVICE=...` in
`/etc/default/dgx-status`.

```bash
sed -e "s|@USER@|$USER|g" -e "s|@REPO@|$REPO|g" "$REPO/deploy/dgx-status.service" \
  | sudo tee /etc/systemd/system/dgx-status.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now dgx-status
journalctl -u dgx-status -f
```

Within a few seconds the log shows `connected to /dev/serial/by-id/...`, and
`display says hello: dgx-status-display, fw 0.1.0`, and the screen shows LIVE.

## Serial port access

The port is `root:dialout 0660`. Pick one:

- **The service (default):** `SupplementaryGroups=dialout` in the unit. Your
  user stays out of `dialout`.
- **Manual runs as your user:** `sudo usermod -aG dialout "$USER"`, then log
  out and in. Then `uv run dgx-status` (or `--device "$PORT"` to pin a port).

## Update the firmware

Once the service runs, it holds the port. Stop it for every esptool command,
and start it again afterwards.

```bash
cd "$REPO"
git pull --ff-only
uv run python scripts/firmware.py build
sudo systemctl stop dgx-status
docker run --rm --device="$DEV" -v "$REPO:/project" -w /project/firmware/build "$IDF_IMAGE" \
  esptool.py --chip esp32c6 -p "$DEV" -b 460800 --before default_reset --after hard_reset \
  write_flash "@flash_args"
sudo systemctl start dgx-status
```

## Read a crash

The board's own log comes over USB, and the daemon relays its errors and
warnings, and every reboot, to the journal (`journalctl -u dgx-status`). The
board also keeps a core dump in flash after a panic or a watchdog reset, and
its next hello says why it reset. To read
the dump (esptool resets the chip, so stop the service first):

```bash
cd "$REPO"
sudo systemctl stop dgx-status
uv run python scripts/firmware.py coredump --port "$PORT" \
  --elf firmware/elf/dgx_status_display-<elf>.elf
sudo systemctl start dgx-status
```

`<elf>` is the `elf` value from the hello line in the daemon log. Without
`--elf` the step uses the current build's ELF. It prints the panic reason,
the crashed task and its backtrace, and saves the raw core in
`firmware/coredump/`; `--core <file>` decodes a saved core again without the
board.

## Restore the factory firmware

```bash
cd "$REPO/backup" && sha256sum -c factory-16MB.bin.sha256 && cd "$REPO"
sudo systemctl stop dgx-status        # if the service is installed
docker run --rm --device="$DEV" -v "$REPO:/project" -w /project "$IDF_IMAGE" \
  esptool.py --chip esp32c6 -p "$DEV" -b 460800 write_flash 0 backup/factory-16MB.bin
sudo systemctl disable --now dgx-status   # the factory app does not read frames
```

## What the screen shows

The panel is about 40 mm across, so every value is big. Three pages of four
tiles cycle every 10 seconds. Each tile has a label, one value and a unit.

| Page | Tiles |
| --- | --- |
| GPU | util %, power W, GPU °C, what is on the GPU (`idle` or the backend) |
| Cooling | fan 0 and fan 1 in kRPM (2700 RPM shows as `2.7`), board °C, NVMe °C |
| Host | CPU %, memory used (with `/ total GiB` as the unit), load 1m, uptime |

![The three pages and the NO DATA screen](docs/img/pages.png)

- **Top strip:** a green LIVE dot (amber, with the age, when STALE), the host
  clock, and one dot per page.
- **Touch:** a tap moves to the next page and restarts the 10-second timer.
  A long press opens every temperature sensor; a tap or 20 seconds goes back.
- **NO DATA** (no frame for over 10 s): the carousel stops; the dimmed screen
  says "DGX not sending" and how long ago the last frame came.
- **Colors:** GPU °C turns amber at 80 and red at 90; board and NVMe °C at
  70 and 80. Everything else stays gray.
- **Numbers hold still:** values use JetBrains Mono, right-aligned against a
  fixed edge, with fixed decimal places, so the last digit keeps its column.
- **Burn-in care:** black background, outlined tiles with no fills, the
  layout moves along a 10-pixel orbit every 3 minutes, a gentle dim after
  10 minutes with no change (no tap, no link change, no new GPU occupant),
  dimmest in NO DATA. The page changes help too.

Each value is one instantaneous reading. An unknown value shows `—`.

## Development

```bash
uv sync
uv run pre-commit install
uv run ruff format --check . && uv run ruff check .
uv run mypy
uv run pytest -q -rs
uv run python scripts/firmware.py test
uv run python scripts/firmware.py build
uv run python scripts/firmware.py preview   # render the UI to firmware/preview/*.png
uv run python scripts/gen_fonts.py --check  # the fonts regenerate byte for byte
uv run python scripts/readme_image.py       # refresh docs/img/pages.png from the preview
```

The preview compiles the real `view.c` with the same LVGL version and
settings as the firmware, on the host, and renders each page to a PNG. It
fails if a value's last digit changes column between two values.

`tests/fixtures/spark/` holds /proc and /sys files copied from a DGX Spark.
`scripts/capture_fixtures.py` copies a fresh set, read-only.

## Credits and license

MIT; see [LICENSE](LICENSE). [NOTICE](NOTICE) lists what came from
elsewhere: the board's pin map, panel init values and power-chip settings
come from Waveshare's demo code for this board.
