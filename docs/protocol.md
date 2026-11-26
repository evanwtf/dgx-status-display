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
{"v":1,"t":1795664697,"host":"test-host","up_s":1214815,"load1":0.35,"load5":0.48,"load15":0.36,"cpu_pct":0.3,"mem_used_gib":7.2,"mem_total_gib":121.7,"gpu_pct":0,"gpu_w":4,"gpu_c":44,"occ":"idle","fan0_rpm":2790,"fan1_rpm":4185,"board_c":45.8,"nvme_c":44.9,"nic_c":47.0,"th":[["tz0",45.8],["tz1",43.9],["nvme",44.9],["nic0",47.0],["wifi",42.0]]}
```

## Display to host: hello

The firmware sends one line when it boots:

```json
{"hello":"dgx-status-display","fw":"0.1.0"}
```

The daemon logs it at INFO. A hello after the first one means the board
rebooted.

## Validation on the display

The firmware accepts a frame only when:

- the line is at most 1024 bytes and is valid JSON with an object at the top;
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
