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
    # vLLM's engine core and workers rename themselves with setproctitle to
    # "VLLM::EngineCore", "VLLM::Worker_TP0" (VLLM_PROCESS_NAME_PREFIX). That
    # rewrites cmdline too, so no argument says "vllm" in lower case.
    ("vllm", ("vllm", "VLLM::")),
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


def _read_comm(base: Path) -> str | None:
    """The kernel cuts comm at 15 bytes, which can split a UTF-8 character.
    Decode with replacement, as for cmdline; never raise."""
    try:
        return (base / "comm").read_bytes().decode(errors="replace").strip()
    except OSError:
        return None


def _read_cmdline(base: Path) -> list[str] | None:
    try:
        raw = (base / "cmdline").read_bytes()
    except OSError:
        return None
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def read_process(proc_root: Path, pid: int) -> Process | None:
    """Read comm and cmdline. None when the process has gone."""
    base = proc_root / str(pid)
    comm = _read_comm(base)
    if comm is None:
        return None
    args = _read_cmdline(base)
    if args is None:
        return None
    return Process(pid=pid, comm=comm, cmdline=args)


class OccupantNamer:
    """Names the GPU occupant each frame, with a cache per PID.

    comm is read every frame (one small read per PID). cmdline is read once
    per PID, and again only when comm changes: that means the PID was reused
    or the process called exec. Entries for PIDs that left the GPU are dropped.
    """

    def __init__(self, proc_root: Path) -> None:
        self._proc = proc_root
        self._cache: dict[int, tuple[str, str]] = {}  # pid -> (comm, name)
        self.cmdline_reads = 0

    def _name(self, pid: int) -> str | None:
        base = self._proc / str(pid)
        comm = _read_comm(base)
        if comm is None:
            return None
        cached = self._cache.get(pid)
        if cached is not None and cached[0] == comm:
            return cached[1]
        args = _read_cmdline(base)
        self.cmdline_reads += 1
        if args is None:
            return None
        name = backend_for(comm, args) or comm or str(pid)
        self._cache[pid] = (comm, name)
        return name

    def describe(self, pids: Iterable[int] | None) -> str | None:
        """The occupant line value.

        - None: unknown. NVML could not list processes, or it listed some and
          none could be read from /proc. The display shows the dash.
        - "idle": NVML listed no compute process. Idle is a real state.
        - Otherwise each distinct backend once, in PID order, joined by ", ".
          A process with no rule shows its own name, not a blank.
        """
        if pids is None:
            return None
        wanted = sorted(set(pids))
        for gone in set(self._cache) - set(wanted):
            del self._cache[gone]
        if not wanted:
            return IDLE
        names: list[str] = []
        for pid in wanted:
            name = self._name(pid)
            if name is not None and name not in names:
                names.append(name)
        return ", ".join(names) if names else None


def describe_occupant(proc_root: Path, pids: Iterable[int] | None) -> str | None:
    """One-shot form of OccupantNamer.describe, with no cache."""
    return OccupantNamer(proc_root).describe(pids)
