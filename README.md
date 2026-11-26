# dgx-status-display

A live status screen for a DGX Spark. A Waveshare ESP32-C6-Touch-AMOLED-2.16
plugs into a USB-C port on the Spark. A small Python daemon on the Spark
sends one JSON frame per second over USB serial, and the board draws it:
GPU use, power and temperature, what is on the GPU, memory, CPU, fans and
every temperature sensor. When the frames stop, the screen says so.

Phase 1 is USB serial only. See [docs/plan.md](docs/plan.md) for phase 2
(Wi-Fi, outlet power, both Sparks) and [docs/protocol.md](docs/protocol.md)
for the frame format.

## Bootstrap

From a bare DGX Spark to a running display. Run each block in order. Docker
must work for your user (`docker ps`). The flash steps run esptool inside the
pinned ESP-IDF image with `--device`, so they need no sudo and no `dialout`
group.

### 1. Set the variables

```bash
export REPO=~/git/dgx-status-display
# The board's stable serial path. List it with: ls -l /dev/serial/by-id/
export PORT=/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00
export DEV=$(readlink -f "$PORT")    # for example /dev/ttyACM0
export IDF_IMAGE=espressif/idf:v5.5.6@sha256:bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84
```

### 2. Get the code and check the daemon

```bash
git clone https://github.com/evanwtf/dgx-status-display "$REPO"
cd "$REPO"
uv sync --frozen
uv run dgx-status --dry-run --once
```

The dry run logs one frame and opens no device. Check it: `occ` matches
`nvidia-smi`, `mem_used_gib` is MemTotal − MemAvailable, and `fan0_rpm` and
`fan1_rpm` are there (they need the `nvfanread` hwmon module).

### 3. Back up the factory firmware

Do this once, before the first flash. esptool resets the board into its
bootloader; the screen goes dark until the next step.

```bash
cd "$REPO"
mkdir -p backup
docker run --rm --device="$DEV" -v "$REPO:/project" -w /project "$IDF_IMAGE" \
  esptool.py --chip esp32c6 -p "$DEV" flash_id
docker run --rm --device="$DEV" -v "$REPO:/project" -w /project "$IDF_IMAGE" \
  bash -c "esptool.py --chip esp32c6 -p $DEV -b 460800 read_flash 0 0x1000000 backup/factory-16MB.bin \
    && chown $(id -u):$(id -g) backup/factory-16MB.bin"
ls -l backup/factory-16MB.bin       # must be 16777216 bytes
sha256sum backup/factory-16MB.bin
```

`flash_id` must report a 16 MB flash. Git ignores `backup/`. Copy the file
off this machine too. Waveshare also publishes its factory image
(`03_Firmware/01_Fac-v1.0.0.bin` in
[waveshareteam/ESP32-C6-Touch-AMOLED-2.16](https://github.com/waveshareteam/ESP32-C6-Touch-AMOLED-2.16)).

### 4. Build and flash the firmware

```bash
cd "$REPO"
uv run python scripts/firmware.py test     # host unit tests
uv run python scripts/firmware.py build    # idf.py build in $IDF_IMAGE
docker run --rm --device="$DEV" -v "$REPO:/project" -w /project/firmware/build "$IDF_IMAGE" \
  esptool.py --chip esp32c6 -p "$DEV" -b 460800 --before default_reset --after hard_reset \
  write_flash "@flash_args"
```

The board restarts and shows "DGX not sending".

### 5. Install the service

The unit runs `$REPO/.venv/bin/dgx-status` as the service user, with `dialout` added
for this service only. Edit `User=`, the paths and `DGX_STATUS_DEVICE` in
the unit if yours differ.

```bash
sudo install -m 644 "$REPO/deploy/dgx-status.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dgx-status
journalctl -u dgx-status -f
```

Within a few seconds the log shows `connected to /dev/serial/by-id/...` and
`display says hello: dgx-status-display, fw 0.1.0`, and the screen shows LIVE.

## Serial port access

The port is `root:dialout 0660`. Pick one:

- **The service (default):** `SupplementaryGroups=dialout` in the unit. Your
  user stays out of `dialout`.
- **Manual runs as your user:** `sudo usermod -aG dialout "$USER"`, then log
  out and in. Then `uv run dgx-status --device "$PORT"`.

## Restore the factory firmware

```bash
docker run --rm --device="$DEV" -v "$REPO:/project" -w /project "$IDF_IMAGE" \
  esptool.py --chip esp32c6 -p "$DEV" -b 460800 write_flash 0 backup/factory-16MB.bin
```

## What the screen shows

- **Overview:** host name and host clock; link state; GPU util %, power (W)
  and temperature; `On GPU: <backend>` or `On GPU: idle`; memory used/total
  GiB; CPU %; load average; fan RPM; board, NVMe and NIC temperatures;
  uptime.
- **Detail** (tap to switch): every temperature sensor, the frame and reject
  counts, and the firmware version.
- **Link state:** LIVE (a frame in the last 3 s), STALE (3–10 s, with the
  age), NO DATA (over 10 s or never: the screen dims and says "DGX not
  sending").
- **Burn-in care:** black background, gray text, the layout moves a few
  pixels every 3 minutes, moderate brightness, dimmer in NO DATA.

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
```

`tests/fixtures/spark/` holds /proc and /sys files copied from a DGX Spark.
`scripts/capture_fixtures.py` copies a fresh set, read-only.
