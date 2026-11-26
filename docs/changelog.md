# Changelog

## 0.1.0 (unreleased)

First version: phase 1, USB serial.

- Protocol v1: one compact JSON line per second, at most 1024 bytes. Only
  `v`, `t` and `host` are required; a missing reading shows as a dash.
- Daemon `dgx-status`: GPU util, power and temperature from NVML; the GPU
  occupant from NVML compute processes and /proc; memory as MemTotal −
  MemAvailable; CPU % from consecutive /proc/stat samples; load, uptime;
  fans from the `nvfanread` hwmon device; board, NVMe and NIC temperatures.
  No SM clock. It reopens the serial port with backoff after an unplug.
- Firmware 0.1.0 for the Waveshare ESP32-C6-Touch-AMOLED-2.16: overview and
  detail pages, LIVE / STALE / NO DATA, burn-in care (pixel shift, gray text,
  dimming), a hello line with the firmware version. Pins and panel init come
  from Waveshare's demo code.
- The daemon finds the board's `/dev/serial/by-id/` port itself;
  `DGX_STATUS_DEVICE` takes a path or a glob.
- systemd unit as a template (`@USER@`, `@REPO@`) with sandboxing, and CI on
  the self-hosted runner, with a fork guard.
- MIT license; NOTICE credits the Waveshare hardware facts. A test keeps
  private infrastructure out of every file.
