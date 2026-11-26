# dgx-status-display

[![check](https://github.com/evanwtf/dgx-status-display/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/evanwtf/dgx-status-display/actions/workflows/check.yml?query=branch%3Amain)
[![Release](https://img.shields.io/github/v/release/evanwtf/dgx-status-display)](https://github.com/evanwtf/dgx-status-display/releases/latest)
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
`--before no_reset` in place of `--before default_reset`. Then replug the
board: it has no reset button.

### 5. Install the service

`deploy/dgx-status.service` is a template. The install fills in `@USER@`
(the account that runs the daemon) and `@REPO@` (the checkout). The unit
adds `dialout` for this service only. The daemon finds the board's port
itself; to pin one port, put `DGX_STATUS_DEVICE=...` in
`/etc/default/dgx-status`.

The network and storage tiles find their sources each second. Set one of
these in the same file only when the automatic choice is wrong
(`.env.example` lists them all):

| Variable | Default |
| --- | --- |
| `DGX_STATUS_LAN_IFACE` | the interface with the default route |
| `DGX_STATUS_ROCE_PORTS` | every RDMA port on an Ethernet link layer, as `device/port` |
| `DGX_STATUS_CLUSTER_IFACES` | every `mlx5_core` interface |
| `DGX_STATUS_SSD_DISK` | the disk that holds `DGX_STATUS_DISK_PATH` (`nvme0n1`) |
| `DGX_STATUS_DISK_PATH` | `/` |

```bash
sed -e "s|@USER@|$USER|g" -e "s|@REPO@|$REPO|g" "$REPO/deploy/dgx-status.service" \
  | sudo tee /etc/systemd/system/dgx-status.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now dgx-status
journalctl -u dgx-status -f
```

Within a few seconds the log shows `connected to /dev/serial/by-id/...`, and
`display says hello: dgx-status-display, fw 0.0.1`, and the screen shows LIVE.

## Install from a release

Each [release](https://github.com/evanwtf/dgx-status-display/releases) holds
the built firmware, so you need no firmware build. The daemon comes from a
clone of the same tag, with its locked dependencies. These steps take the
place of bootstrap steps 2 and 4. Do step 1 (the variables) first.

Get the daemon at the release tag and check it. This replaces step 2:

```bash
export VERSION=0.0.1
git clone --branch "v$VERSION" https://github.com/evanwtf/dgx-status-display "$REPO"
cd "$REPO" && uv sync --frozen --no-dev
uv run --frozen --no-dev dgx-status --version
uv run --frozen --no-dev dgx-status --dry-run --once
```

Do step 3 now (back up the factory firmware). Then download the firmware
and check it:

```bash
mkdir -p "$REPO/release" && cd "$REPO/release"
BASE=https://github.com/evanwtf/dgx-status-display/releases/download/v$VERSION
for f in SHA256SUMS "dgx-status-display-$VERSION-esp32c6-merged.bin" \
    "dgx-status-display-$VERSION-esp32c6.zip"; do curl -fsSLO "$BASE/$f"; done
sha256sum --ignore-missing -c SHA256SUMS    # each file must say OK
```

**First install:** flash the merged image at 0x0. It holds the bootloader,
the partition table and the app, with 0xFF between them. It erases the
board's stored settings (the screen rotation and the chart style), so use it
only for a first install:

```bash
docker run --rm --device="$DEV" -v "$REPO/release:/release:ro" -w /release "$IDF_IMAGE" \
  esptool.py --chip esp32c6 -p "$DEV" -b 460800 --before default_reset --after hard_reset \
  write_flash 0x0 "dgx-status-display-$VERSION-esp32c6-merged.bin"
```

Then install the service (step 5).

**Upgrade:** flash the parts from the zip with `flash_args`. This writes only
the bootloader, the partition table and the app, so the settings stay. The
service holds the port, so stop it first:

```bash
cd "$REPO/release"
python3 -m zipfile -e "dgx-status-display-$VERSION-esp32c6.zip" .
sudo systemctl stop dgx-status
docker run --rm --device="$DEV" -v "$REPO/release:/release:ro" \
  -w "/release/dgx-status-display-$VERSION-esp32c6" "$IDF_IMAGE" \
  esptool.py --chip esp32c6 -p "$DEV" -b 460800 --before default_reset --after hard_reset \
  write_flash "@flash_args"
sudo systemctl start dgx-status
```

To upgrade the daemon too, check out the new tag in `$REPO` and run
`uv sync --frozen --no-dev` before you start the service.

The release also has `dgx-status-display-$VERSION-esp32c6.elf` to decode a
core dump (`scripts/firmware.py coredump --elf`), the wheel and the sdist,
and `dgx-status.service`. `SHA256SUMS` covers every file.

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
colored tiles cycle every 10 seconds. Each tile has a label, one value and a
unit.

| Page | Tiles |
| --- | --- |
| GPU | Usage %, Power W, Temp °C, Running (`idle` or the backend) |
| Cooling | Fan 1 and Fan 2 in kRPM (2700 RPM shows as `2.7`), Board °C, SSD °C |
| Host | CPU %, Memory used (`/ 128 GiB`, the memory installed, as the unit), Load avg, Up |

![The three pages and the NO DATA screen](docs/img/pages.png)

- **Color carries the reading.** Usage, Power, the fans, CPU and Memory fill
  from the bottom to the value's fraction (Power against 240 W), and the
  fill eases to a new level in 400 ms. Temperature tiles turn from blue to
  amber at the threshold and red above it, with a bright edge on top:
  GPU 80 / 90 °C, board and SSD 70 / 80 °C.
- **Charts (option):** on the detail page (long press), the "Charts" row
  switches between **Level** (the default) and **2-minute history**: the
  tile's background becomes an area chart of its last 120 one-second
  samples, on a fixed scale (0–100 %, 0–240 W, 0–13500 RPM, 0–total GiB,
  20–100 °C). A missing reading or a NO DATA period is a gap. The numbers
  stay the instantaneous reading. The choice survives a reboot (NVS). The
  history lives on the board and starts empty after a reboot.
- **Top strip:** the page title, the host clock and a link dot (gold LIVE,
  amber STALE with the age).
- **Touch:** a tap moves to the next page and restarts the 10-second timer.
  A long press opens every temperature sensor; a tap or 20 seconds goes back.
- **Buttons:** a short press of **KEY** (released within 1 s) turns the screen 90° clockwise, so the board can stand on any
  edge. The angle survives a reboot (NVS), and touch follows it. BOOT and PWR
  have no action; the board logs every press. Holding PWR does not switch
  the board off: unplug it.
- **NO DATA** (no frame for over 10 s): the carousel stops; a black, dimmed
  screen says "No data from the DGX" and how long ago the last frame came.
- **Type:** values in JetBrains Mono, right-aligned against a fixed edge with
  fixed decimals, so the last digit keeps its column; words in Barlow Semi
  Condensed. Every text color passes a contrast test (values 7:1, labels
  4.5:1) over every shade it can sit on.
- **Burn-in care:** black ground and gutters, the layout moves along a
  10-pixel orbit every 3 minutes, a gentle dim after 10 minutes with no
  change, and the pages put different hues in the same places.

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

### Cut a release

A `v*` tag runs `.github/workflows/release.yml`. It refuses the tag unless
the three declared versions equal it, the tagged commit is on `main`, and
`docs/changelog.md` has one non-empty `## X.Y.Z (YYYY-MM-DD)` section for
it. It runs every check, builds the firmware and the wheel, checks that each
build carries the tag's version, and publishes the release with the notes
from the changelog. The tag message holds no notes.

1. Rename the changelog's top section to `## X.Y.Z (YYYY-MM-DD)`.
2. Set `X.Y.Z` in `pyproject.toml`, `src/dgx_status/__init__.py` and
   `PROJECT_VER` in `firmware/CMakeLists.txt`, then run `uv lock`.
3. Check, commit and push to `main`:

   ```bash
   export VERSION=X.Y.Z
   uv run python scripts/release_check.py version "v$VERSION"
   uv run python scripts/release_check.py notes "$VERSION" "$(mktemp)"
   git push origin main
   ```

4. Tag and push the tag:

   ```bash
   git tag -a "v$VERSION" -m "v$VERSION" && git push origin "v$VERSION"
   ```

## Credits and license

MIT; see [LICENSE](LICENSE). [NOTICE](NOTICE) lists what came from
elsewhere: the board's pin map, panel init values and power-chip settings
come from Waveshare's demo code for this board.
