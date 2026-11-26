"""Name what is on the GPU: a backend name, or "idle".

The input is the PID list from NVML (`nvmlDeviceGetComputeRunningProcesses`).
Each PID maps to a backend by its process name or command line, read from
/proc. This module never shells out.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

IDLE = "idle"

# (backend, substrings). The first rule with a match wins. A substring matches
# the comm or any command-line argument (including the basename of argv[0]).
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("llama.cpp", ("llama-server", "llama-cli", "llama-bench")),
    ("ds4", ("ds4-server",)),
    ("vllm", ("vllm",)),
    ("ollama", ("ollama",)),
    ("sglang", ("sglang",)),
    ("trt-llm", ("trtllm", "tensorrt_llm")),
    ("tensorfold", ("tensorfold", "tf-dsv41")),
    ("comfyui", ("ComfyUI", "comfyui")),
)


@dataclass(frozen=True)
class Process:
    pid: int
    comm: str
    cmdline: list[str]


def backend_for(comm: str, cmdline: list[str]) -> str | None:
    """The backend name for one process, or None when no rule matches."""
    haystack = [comm, *cmdline, *(Path(a).name for a in cmdline[:1])]
    for backend, needles in _RULES:
        if any(n in h for n in needles for h in haystack):
            return backend
    return None


def read_process(proc_root: Path, pid: int) -> Process | None:
    """Read comm and cmdline. None when the process has gone."""
    base = proc_root / str(pid)
    try:
        comm = (base / "comm").read_text().strip()
        raw = (base / "cmdline").read_bytes()
    except OSError:
        return None
    args = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
    return Process(pid=pid, comm=comm, cmdline=args)


def describe_occupant(proc_root: Path, pids: Iterable[int] | None) -> str | None:
    """The occupant line value.

    - None: NVML could not list processes. The display shows the dash.
    - "idle": no compute process is on the GPU. Idle is a real state.
    - Otherwise each distinct backend once, in PID order, joined by ", ".
      A process with no rule shows its own name, not a blank.
    """
    if pids is None:
        return None
    names: list[str] = []
    for pid in sorted(set(pids)):
        proc = read_process(proc_root, pid)
        if proc is None:
            continue
        name = backend_for(proc.comm, proc.cmdline) or proc.comm or str(pid)
        if name not in names:
            names.append(name)
    return ", ".join(names) if names else IDLE
