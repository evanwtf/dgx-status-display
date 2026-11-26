# Protocol v1

The host daemon (`dgx-status`) sends status frames to the display over USB
serial (USB-Serial-JTAG on the ESP32-C6). The display sends one `hello` line
back when it boots.

## Framing

- One JSON object per line. The line ends with `\n` (0x0A).
- A line is at most **1024 bytes**, newline included. The firmware rejects a
  longer line and counts it as a reject.
- The JSON is compact (no spaces) and plain ASCII. Non-ASCII characters are
  sent as `\uXXXX` escapes.
- The daemon sends one frame per second.

## Host to display: the status frame

Each frame holds one instantaneous reading of each value. No value is an
average over time, a range or a rolling window. (Load average is the
kernel's own metric, sent as it is.)

Three fields are required. The firmware rejects a frame without them, or
with `v` not equal to 1.

| Key | Type | Meaning |
| --- | --- | --- |
| `v` | int | Protocol version. Always `1`. |
| `t` | int | Host clock, Unix epoch seconds. The display shows this time. |
| `host` | string | Host short name, at most 32 characters. |

Every other field is optional. **A missing field means "unknown".** The
display draws an unknown value as `—`. The daemon leaves a field out when it
cannot read it. It never sends `null`, `NaN` or a placeholder number.

| Key | Type | Unit | Source |
| --- | --- | --- | --- |
| `tz_s` | int | s | Host UTC offset, from −50400 to 50400. The display adds it to `t` to show local time. |
| `up_s` | int | s | `/proc/uptime`, first field |
| `load1`, `load5`, `load15` | number | — | `/proc/loadavg` |
| `cpu_pct` | number | % | `/proc/stat` `cpu` line, delta between this frame and the last |
| `mem_used_gib` | number | GiB | `MemTotal − MemAvailable` from `/proc/meminfo`. Never `MemFree`. |
| `mem_total_gib` | number | GiB | Installed memory: the online blocks in `/sys/devices/system/memory/memory*/online` × `block_size_bytes`. Not `MemTotal`: firmware and the kernel keep about 6 GiB before `MemTotal` (121.7 GiB on a Spark, against 127.9 GiB installed). |
| `gpu_pct` | int | % | NVML utilization |
| `gpu_w` | int | W | NVML power, rounded to an integer |
| `gpu_c` | int | °C | NVML GPU temperature |
| `occ` | string | — | What is on the GPU: a backend name (`llama.cpp`, `ds4`, `vllm`, `ollama`, ...), or `idle`. At most 48 characters. |
| `fan0_rpm`, `fan1_rpm` | int | RPM | hwmon device named `nvfanread` |
| `board_c` | number | °C | Max of the `acpitz` thermal zones |
| `nvme_c` | number | °C | hwmon `nvme`, the `Composite` sensor |
| `nic_c` | number | °C | Max of the hwmon `mlx5` devices |
| `th` | array | °C | Every sensor, for the detail page: `[["tz0",45.8],["nvme",44.9],...]`. Labels are at most 15 characters. |
| `lan_rx_bps`, `lan_tx_bps` | int | bit/s | The LAN interface's `statistics/rx_bytes` and `tx_bytes` × 8, delta between this frame and the last. The LAN is the interface with the default route in `/proc/net/route`. |
| `lan_mbps` | int | Mb/s | The LAN's link speed, `/sys/class/net/<lan>/speed`. Left out when the link is down (the file reads −1). The display's full scale for `lan_*`. |
| `roce_rx_bps`, `roce_tx_bps` | int | bit/s | RoCE traffic: the sum over every `/sys/class/infiniband/*/ports/*` whose `link_layer` is `Ethernet`, of `counters/port_rcv_data` and `port_xmit_data`. Those count 4-byte words, so bits = delta × 32. |
| `cip_bps` | int | bit/s | IP traffic on the cluster NIC, in + out: `statistics/rx_bytes` + `tx_bytes` × 8 over the interfaces whose driver is `mlx5_core`. It excludes RoCE (below). |
| `ssd_rd_Bps`, `ssd_wr_Bps` | int | byte/s | `/sys/block/<disk>/stat` sectors read (field 3) and written (field 7) × 512, delta. The disk is the one that holds `/`. (`B` is bytes, `b` is bits.) |
| `disk_used_gb`, `disk_size_gb` | int | GB (10⁹ B) | `statvfs("/")`: size = `f_blocks × f_frsize`, used = `(f_blocks − f_bfree) × f_frsize`, as `df` counts them. Sent together, or not at all. |

Numbers other than the integers carry one decimal place (`load*`: two).

### Rates

Every rate is the delta of a kernel counter between this frame and the last,
divided by the time between the two reads (the daemon's monotonic clock).
It is the instant reading, the same as `cpu_pct`, never an average over a
window. A rate is left out (unknown) when:

- it is the first frame, or the previous read failed;
- any counter in the sum went down (a reset or a wrap);
- the set of interfaces or devices in the sum changed (one appeared or went
  away).

An environment variable can replace each automatic choice: see the README.

**What the counters count** (Linux 7.0, mlx5):

- `port_xmit_data` and `port_rcv_data` on an Ethernet link layer count RoCE
  traffic only. `drivers/infiniband/hw/mlx5/mad.c`, `pma_cnt_ext_assign()`:
  they are the vport `transmitted_ib_unicast` + `transmitted_ib_multicast`
  octets (and `received_ib_*`), shifted right by 2 (4-byte words). The sysfs
  files are the 64-bit `PortCountersExt` attributes
  (`drivers/infiniband/core/sysfs.c`, `pma_group_ext`), because mlx5 sets
  `IB_PMA_CLASS_CAP_EXT_WIDTH`. On a Spark they equal ethtool's
  `*_vport_rdma_unicast_bytes` / 4.
- The mlx5 netdev `statistics/*_bytes` count only what passes the kernel's
  own send and receive rings, so RDMA traffic is not in them.
  `drivers/net/ethernet/mellanox/mlx5/core/en_main.c`, `mlx5e_get_stats()`
  folds the software ring counters (`mlx5e_fold_sw_stats64()`). In
  switchdev mode it reports the port's 802.3 counters instead, which do
  include RoCE. On a Spark the netdev counters differ from ethtool's
  `tx_bytes_phy`, so it runs in legacy mode.

The frame has no SM clock field, by design. GPU memory is not sent: NVML
reports N/A on GB10, because the memory pool is unified. `mem_*` covers it.

`occ` and the GPU fields come from NVML. When NVML fails, `occ` is left out
(unknown). It is not `idle`. `idle` means NVML listed no compute processes.

### Size cap

The daemon truncates `host`, `occ` and the `th` labels. If a frame is still
over 1024 bytes, it drops `th` entries from the end until the frame fits. The
overview fields always fit: every one at its longest takes 639 bytes.

### Example

```json
{"v":1,"t":1795664697,"tz_s":-18000,"host":"test-host","up_s":1214815,"load1":0.35,"load5":0.48,"load15":0.36,"cpu_pct":0.3,"mem_used_gib":7.2,"mem_total_gib":127.9,"gpu_pct":0,"gpu_w":4,"gpu_c":44,"occ":"idle","fan0_rpm":2790,"fan1_rpm":4185,"board_c":45.8,"nvme_c":44.9,"nic_c":47.0,"th":[["tz0",45.8],["tz1",43.9],["nvme",44.9],["nic0",47.0],["wifi",42.0]],"lan_rx_bps":32640,"lan_tx_bps":67401,"lan_mbps":2500,"roce_rx_bps":0,"roce_tx_bps":0,"cip_bps":3680,"ssd_rd_Bps":115194000,"ssd_wr_Bps":767000,"disk_used_gb":3340,"disk_size_gb":4032}
```

## Display to host: hello and log lines

The display sends JSON lines too. Any line that is not a JSON object is a log
line (below).

### hello

One line when the firmware boots, and again when frames start after a NO DATA
period (for example, after the daemon restarts):

```json
{"hello":"dgx-status-display","fw":"0.1.0","elf":"a32ef43a44370710","rst":"task_wdt","why":"Task watchdog got triggered."}
```

| Field | Meaning |
| --- | --- |
| `hello`, `fw` | Always present. The firmware version. |
| `elf` | Optional. The first 16 hex digits of the SHA-256 of the build's ELF file; `scripts/firmware.py build` keeps that ELF as `firmware/elf/dgx_status_display-<elf>.elf`. |
| `rst` | Optional. Why the chip last reset (`esp_reset_reason()`): `poweron`, `sw`, `usb`, `panic`, `task_wdt`, `int_wdt`, `wdt`, `brownout`, `cpu_lockup`, ... |
| `why` | Optional, only after a crash reset: the panic reason from the core dump in flash, at most 96 characters, JSON-escaped. |

The daemon logs the hello at INFO. When `rst` is `panic`, `task_wdt`,
`int_wdt`, `wdt` or `cpu_lockup`, it also logs a WARNING with `why`. The
hello can be lost: the board sends it whether or not the host has the port
open.

### Log lines

USB-Serial-JTAG is also the board's secondary console, on purpose
(`firmware/sdkconfig.defaults`): the ROM boot banner and every ESP-IDF log
line come out here, mixed with the JSON lines. The daemon never reads them
as data. It relays them to its own log:

| Line | Daemon log |
| --- | --- |
| `E (…) tag: msg`, `W (…) tag: msg` | WARNING, at most once per distinct message per 60 s; the next one says how many were held back |
| `rst:0x… (…)` (the ROM's reset line) | WARNING, every time, so each reboot is in the journal |
| `I`, `D`, `V` lines, the rest of the banner, anything else | DEBUG |

Once a minute the firmware logs its stack and heap high-water marks
(`ui: free: stack rx … B, lvgl … B; lv heap …% used, …`) at INFO, or at WARN
when a stack has under 768 bytes left or the LVGL heap is 85 % used.

Log output and the hello share the port without a lock, so a log line can
split a hello in rare cases; that hello is then relayed as a log line.

## Validation on the display

The firmware accepts a frame only when:

- the line is at most 1024 bytes and is valid JSON with an object at the top;
- `[`/`{` nesting, counted outside strings, is at most 4 deep (a real frame
  is 3 deep). The firmware checks this before it parses, so deep input
  cannot exhaust the parser's stack;
- `v` is 1, `t` is a number, and `host` is a non-empty string.

It drops a field whose value is the wrong type, not finite, or out of range.
That field then shows `—`; the rest of the frame stays. Ranges:

| Field | Range |
| --- | --- |
| `cpu_pct`, `gpu_pct` | 0 to 100 |
| `gpu_w` | 0 to 2000 |
| temperatures | −40 to 150 |
| `fan*_rpm` | 0 to 30000 |
| `mem_*_gib` | 0 to 4096, and used ≤ total |
| rates (`*_bps`, `*_Bps`) | 0 to 10¹³ |
| `lan_mbps` | 1 to 10⁷ |
| `disk_*_gb` | 0 to 10⁹, both present, and used ≤ size |
| `load*` | 0 to 10000 |
| `up_s` | 0 or more |

A rejected line increments a counter. The detail page shows the counter.

## Link state on the display

The display measures age from the last **accepted** frame, on its own
monotonic clock. It does not compare `t` to anything, so it needs no RTC.

| State | Age of last accepted frame | Screen |
| --- | --- | --- |
| LIVE | 3 s or less | Normal |
| STALE | more than 3 s, up to 10 s | Normal, plus "STALE 7s" |
| NO DATA | more than 10 s, or no frame since boot | Dim. "DGX not sending" and the age of the last frame, or "never". |

## Versions

A change that an old reader would misread gets a new `v`. Adding an optional
field does not: readers ignore keys they do not know.
