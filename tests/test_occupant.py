"""Map GPU compute processes to a backend name, read from /proc."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_status.occupant import backend_for, describe_occupant, read_process


@pytest.mark.parametrize(
    ("comm", "cmdline", "backend"),
    [
        (
            "llama-server",
            ["/opt/llama.cpp/build/bin/llama-server", "-m", "x.gguf"],
            "llama.cpp",
        ),
        ("ds4-server", ["./ds4-server", "--port", "8020"], "ds4"),
        # vLLM's worker renames its thread; the cmdline still says vllm.
        (
            "pt_main_thread",
            ["/venv/bin/python", "-m", "vllm.entrypoints.openai.api_server"],
            "vllm",
        ),
        ("vllm", ["/venv/bin/vllm", "serve", "Qwen/Qwen3"], "vllm"),
        ("ollama", ["/usr/local/bin/ollama", "runner", "--model", "/m/blob"], "ollama"),
        ("python3", ["python3", "-m", "sglang.launch_server"], "sglang"),
        ("python", ["python", "main.py", "--listen"], None),
        ("trtllm-serve", ["trtllm-serve", "model"], "trt-llm"),
    ],
)
def test_backend_for(comm: str, cmdline: list[str], backend: str | None) -> None:
    assert backend_for(comm, cmdline) == backend


def test_read_real_ollama_process(proc_root: Path) -> None:
    """`ollama serve`, captured from the DGX on 2026-11-25."""
    proc = read_process(proc_root, 3558250)
    assert proc is not None
    assert proc.comm == "ollama"
    assert proc.cmdline == ["/usr/local/bin/ollama", "serve"]
    assert backend_for(proc.comm, proc.cmdline) == "ollama"


def test_read_missing_process_is_none(proc_root: Path) -> None:
    """A process can exit between the NVML call and the /proc read."""
    assert read_process(proc_root, 999_999_999) is None


def test_no_pids_is_idle(proc_root: Path) -> None:
    assert describe_occupant(proc_root, []) == "idle"


def test_nvml_unknown_is_none(proc_root: Path) -> None:
    """None means NVML could not say. That is not idle."""
    assert describe_occupant(proc_root, None) is None


def test_known_pid_is_named(proc_root: Path) -> None:
    assert describe_occupant(proc_root, [3558250]) == "ollama"


def test_unmapped_process_falls_back_to_its_name(tmp_path: Path) -> None:
    p = tmp_path / "42"
    p.mkdir()
    (p / "comm").write_text("my-trainer\n")
    (p / "cmdline").write_bytes(b"/usr/bin/my-trainer\0--epochs\0003\0")
    assert describe_occupant(tmp_path, [42]) == "my-trainer"


def test_several_backends_are_listed_once_each(tmp_path: Path) -> None:
    for pid, comm, cmd in [
        (1, "llama-server", b"llama-server\0-m\0a\0"),
        (2, "llama-server", b"llama-server\0-m\0b\0"),
        (3, "ollama", b"ollama\0runner\0"),
    ]:
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes(cmd)
    assert describe_occupant(tmp_path, [1, 2, 3]) == "llama.cpp, ollama"


def test_pids_that_all_vanished_read_as_idle(proc_root: Path) -> None:
    assert describe_occupant(proc_root, [999_999_999]) == "idle"
