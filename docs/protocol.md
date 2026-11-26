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
| `mem_total_gib` | number | GiB | `MemTotal` |
| `gpu_pct` | int | % | NVML utilization |
| `gpu_w` | int | W | NVML power, rounded to an integer |
| `gpu_c` | int | °C | NVML GPU temperature |
| `occ` | string | — | What is on the GPU: a backend name (`llama.cpp`, `ds4`, `vllm`, `ollama`, ...), or `idle`. At most 48 characters. |
| `fan0_rpm`, `fan1_rpm` | int | RPM | hwmon device named `nvfanread` |
| `board_c` | number | °C | Max of the `acpitz` thermal zones |
| `nvme_c` | number | °C | hwmon `nvme`, the `Composite` sensor |
| `nic_c` | number | °C | Max of the hwmon `mlx5` devices |
| `th` | array | °C | Every sensor, for the detail page: `[["tz0",45.8],["nvme",44.9],...]`. Labels are at most 15 characters. |

Numbers other than the integers carry one decimal place (`load*`: two).

The frame has no SM clock field, by design. GPU memory is not sent: NVML
reports N/A on GB10, because the memory pool is unified. `mem_*` covers it.

`occ` and the GPU fields come from NVML. When NVML fails, `occ` is left out
(unknown). It is not `idle`. `idle` means NVML listed no compute processes.

### Size cap

The daemon truncates `host`, `occ` and the `th` labels. If a frame is still
over 1024 bytes, it drops `th` entries from the end until the frame fits. The
overview fields always fit.

### Example

```json
{"v":1,"t":1795664697,"tz_s":-18000,"host":"test-host","up_s":1214815,"load1":0.35,"load5":0.48,"load15":0.36,"cpu_pct":0.3,"mem_used_gib":7.2,"mem_total_gib":121.7,"gpu_pct":0,"gpu_w":4,"gpu_c":44,"occ":"idle","fan0_rpm":2790,"fan1_rpm":4185,"board_c":45.8,"nvme_c":44.9,"nic_c":47.0,"th":[["tz0",45.8],["tz1",43.9],["nvme",44.9],["nic0",47.0],["wifi",42.0]]}
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
