# Plan

## Phase 1: USB serial (built)

The board takes power and data from one USB-C port on the DGX Spark. A DGX
that is fully off gives a dark screen. That is accepted for phase 1.

Done in the repo:

- [x] Protocol v1 ([protocol.md](protocol.md)).
- [x] Daemon: collectors for /proc, thermal zones, hwmon (with nvfanread),
  NVML and the GPU occupant; frame encoder with the 1024-byte cap; serial
  link that survives unplug and replug; `--dry-run` and `--once`.
- [x] Firmware: AXP2101 and CO5300 bring-up, LVGL through esp_lvgl_port with
  partial buffers, USB-Serial-JTAG line reader, cJSON parser, overview and
  detail pages, LIVE / STALE / NO DATA, burn-in care, hello line.
- [x] Host unit tests for the firmware logic, under ASan and UBSan.
- [x] systemd unit with sandboxing.
- [x] CI on the self-hosted Linux runner: Python checks, firmware tests and
  build.

Left for phase 1, on the hardware:

1. Back up the factory firmware (README, step 3).
2. Flash and check the first light: panel orientation, colors (RGB565 byte
   order), touch, and that the board does not reset when the daemon opens
   the port.
3. Install the service. Unplug and replug the board; check the log shows one
   "lost" line and one "connected" line, and the screen goes from NO DATA to
   LIVE.
4. Leave it a day. Look for burn-in and check the pixel shift.

Not yet set up: release automation. Add it (tag check against
`pyproject.toml`, `__init__.py` and the firmware `PROJECT_VER`; notes from
this changelog) before the first tagged release.

## Phase 2 (planned, not built)

### Wi-Fi transport

The board moves to its own USB charger, so the screen stays lit when the DGX
is off. It then shows "DGX not sending" with an age, which is the real state.

- The board joins Wi-Fi as a station. Credentials come from NVS, set once
  over USB serial with a provisioning command. They never go in the
  firmware image or the repo.
- Transport: the daemon sends the same v1 frames over UDP to the board, one
  datagram per frame. Each frame already fits in 1024 bytes, so it fits one
  datagram. UDP has no connection to re-open, and a lost frame is the same
  as a late one: the link state handles both.
- Addressing: the board announces itself with mDNS
  (`_dgxstatus._udp`). The daemon sends to the resolved address, with a
  `--target host:port` override.
- Trust: the LAN is trusted, but a frame should not be forgeable by any host
  on it. Add an HMAC-SHA256 over the line with a shared key (key in NVS on
  the board, in 1Password on the host). The frame gets a `mac` field; v
  stays 1, because an old reader ignores unknown keys. Reject a frame whose
  `t` is older than the last accepted one, to stop replays.
- USB serial stays as a fallback and for provisioning.
- The C6 radio and LVGL share the 512 KB SRAM. Measure the free heap with
  Wi-Fi up before choosing buffer sizes; the draw buffers may need to
  shrink from 40 lines to 20.

### Whole-system power from the smart plug

The DGX's outlet power comes from Evan's Home Assistant smart plug. It
covers the whole box, which NVML GPU power does not, and it still reads when
the DGX is off.

- The host daemon cannot report it when the DGX is down. So a separate
  sender reads it: a small service on an always-on machine polls the Home
  Assistant REST API (`/api/states/<sensor entity>`) once a second and sends
  it to the board in its own frame (`{"v":1,"t":...,"host":"ha","outlet_w":...}`).
  The board keeps one state per `host`.
- The Home Assistant long-lived access token lives in 1Password, vault
  "redacted", item `redacted`. The service reads it at
  start with `op read`. It never goes in the repo, a unit file or a log.
  Set `httpx` and `httpcore` loggers to WARNING so the request URL and
  headers never reach a log.
- The overview shows `Outlet 212 W` next to `GPU 81 W`. With the DGX off,
  the screen shows outlet power and "DGX not sending" together, which tells
  "off" apart from "hung".

### Both DGX Sparks on one screen

- Each daemon already sends `host`. The board keeps one state and one link
  timer per host, up to 4.
- Overview: two columns, one per Spark, with the big figures (GPU W, GPU °C,
  occupant, memory) and the link state of each. A tap opens a detail page per
  host.
- Each Spark keeps its own link state. One Spark in NO DATA does not dim the
  whole screen; only its column dims.
- With Wi-Fi this needs nothing on the host side but the target address. Over
  USB it is not possible: the board has one USB port.

### Order

Wi-Fi first, since both later items need it. Then both Sparks (protocol
already fits). Then outlet power (a new sender and a secret).
