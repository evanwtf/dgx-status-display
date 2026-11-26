"""The LLM exporter reader: parser, engine selection and step rates."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_status.llm import (
    LlmPoller,
    LlmReading,
    Step,
    StepRate,
    StepRatio,
    parse_prometheus,
    reduce,
)

FIX = Path(__file__).parent / "fixtures" / "llme"


def _text(name: str) -> str:
    return (FIX / name).read_text()


def test_parser_reads_labels_values_and_timestamps() -> None:
    text = (
        "# HELP x a metric\n"
        "# TYPE x counter\n"
        'x{a="1",b="q\\"uote\\\\ and \\n",c=""} 3.5 1700000000000\n'
        "plain 7\n"
        'spaced{ a = "v" } -1e3\n'
        "inf_value +Inf\n"
    )
    got = parse_prometheus(text)
    assert got[0].name == "x"
    assert got[0].labels == {"a": "1", "b": 'q"uote\\ and \n', "c": ""}
    assert got[0].value == 3.5
    assert (got[1].name, got[1].labels, got[1].value) == ("plain", {}, 7.0)
    assert got[2].labels == {"a": "v"} and got[2].value == -1000.0
    assert got[3].value == float("inf")


@pytest.mark.parametrize(
    "line",
    ['x{a="1" 3', "x{a=1} 3", "x{} notanumber", "x", 'x{a="unterminated} 1'],
)
def test_parser_skips_malformed_lines(line: str) -> None:
    assert parse_prometheus(line + "\nok 1\n")[-1].name == "ok"
    assert len(parse_prometheus(line)) == 0


def test_reduce_sglang_fixture() -> None:
    e = reduce(parse_prometheus(_text("sglang.txt")))
    assert e.engines_up == 1
    assert e.models == ["MiMo-v2.6-Flash"]
    assert sum(e.decode.values()) == 156755
    assert sum(e.prefill.values()) == 971400
    assert e.requests == 1
    assert e.kv_ratio == pytest.approx(0.14)
    assert sum(e.ttft_count.values()) == 183
    assert sum(e.ttft_sum.values()) == pytest.approx(854.3810183340029)


def test_reduce_llamacpp_busy_and_down() -> None:
    e = reduce(parse_prometheus(_text("llamacpp-busy.txt")))
    assert e.engines_up == 1 and e.models == ["smollm2-135m"]
    assert sum(e.decode.values()) == 224 and sum(e.prefill.values()) == 278
    assert e.kv_ratio is None  # llama.cpp does not export it: unknown, not 0
    down = reduce(parse_prometheus(_text("llamacpp-down.txt")))
    assert down.engines_up == 0 and down.decode == {} and down.requests is None


def test_series_of_down_engines_are_ignored() -> None:
    text = (
        'llme_engine_up{engine="a",model="m1",backend="b",host="h",nodes="1"} 1\n'
        'llme_engine_up{engine="a",model="m2",backend="c",host="h",nodes="1"} 0\n'
        'llme_requests_running{engine="a",model="m1",backend="b",host="h",nodes="1",'
        'worker="default"} 2\n'
        'llme_requests_running{engine="a",model="m2",backend="c",host="h",nodes="1",'
        'worker="default"} 9\n'
    )
    e = reduce(parse_prometheus(text))
    assert e.engines_up == 1 and e.models == ["m1"] and e.requests == 2


def test_step_rate_on_a_per_iteration_engine() -> None:
    """vLLM moves every scheduler step: each 1 s poll is one step."""
    r = StepRate()
    assert r.update(Step(0.0, {"a": 0}), busy=True) is None  # no step yet
    assert r.update(Step(1.0, {"a": 45}), busy=True) == pytest.approx(45.0)
    assert r.update(Step(2.0, {"a": 95}), busy=True) == pytest.approx(50.0)
    # Done: no requests, counter still. A real zero.
    assert r.update(Step(3.0, {"a": 95}), busy=False) == 0.0


def test_step_rate_on_a_finish_updated_engine_does_not_flip() -> None:
    """llama.cpp moves only when a request finishes: hold the step's rate."""
    r = StepRate()
    r.update(Step(0.0, {"a": 0}), busy=True)
    assert r.update(Step(5.0, {"a": 250}), busy=True) == pytest.approx(50.0)
    # Polls in between: no change, still busy. Not 0, not a spike.
    for t in (6.0, 7.0, 8.0):
        assert r.update(Step(t, {"a": 250}), busy=True) == pytest.approx(50.0)
    assert r.update(Step(10.0, {"a": 500}), busy=True) == pytest.approx(50.0)


def test_step_rate_unknown_after_a_long_step_reset_or_new_series() -> None:
    r = StepRate(max_step_s=30.0)
    r.update(Step(0.0, {"a": 0}), busy=True)
    assert r.update(Step(31.0, {"a": 900}), busy=True) is None  # too long a step
    assert r.update(Step(32.0, {"a": 10}), busy=True) is None  # reset
    assert r.update(Step(33.0, {"a": 20, "b": 0}), busy=True) is None  # new worker
    assert r.update(Step(34.0, {"a": 30, "b": 5}), busy=True) == pytest.approx(15.0)
    assert r.update(None, busy=True) is None


def test_step_rate_idle_engine_reads_zero_at_once() -> None:
    r = StepRate()
    assert r.update(Step(0.0, {"a": 7}), busy=False) == 0.0


def test_step_ratio_holds_the_last_value() -> None:
    q = StepRatio()
    assert q.update(Step(0, {"s": 10.0}), Step(0, {"c": 5})) is None
    assert q.update(Step(1, {"s": 10.6}), Step(1, {"c": 7})) == pytest.approx(0.3)
    assert q.update(Step(2, {"s": 10.6}), Step(2, {"c": 7})) == pytest.approx(0.3)
    assert q.update(Step(3, {"s": 9.0}), Step(3, {"c": 8})) is None  # reset
    assert q.update(None, None) is None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Fetcher:
    def __init__(self, body: str | None) -> None:
        self.body = body
        self.timeouts: list[float] = []

    def __call__(self, url: str, timeout: float) -> str:
        self.timeouts.append(timeout)
        if self.body is None:
            raise OSError("connection refused")
        return self.body


def test_poller_states() -> None:
    clock = Clock()
    fetch = Fetcher(None)
    p = LlmPoller("http://127.0.0.1:1/metrics", fetch=fetch, clock=clock)
    p.poll()
    assert p.reading() is None  # unreachable: dashes
    assert fetch.timeouts == [0.3]

    fetch.body = _text("llamacpp-down.txt")
    p.poll()
    assert p.reading() == LlmReading(engines_up=0)  # every engine down: idle

    fetch.body = _text("llamacpp-idle.txt")
    clock.now += 1
    p.poll()
    r = p.reading()
    assert r is not None and r.engines_up == 1
    assert r.out_tps == 0.0 and r.in_tps == 0.0 and r.requests == 0  # real zeros
    assert r.kv_pct is None

    fetch.body = "<html>not metrics</html>"
    clock.now += 1
    p.poll()
    assert p.reading() is None  # bad answer: dashes


def test_poller_rates_from_two_real_expositions() -> None:
    """Idle, then four requests later: 224 decode and 278 prefill tokens."""
    clock = Clock()
    fetch = Fetcher(_text("llamacpp-idle.txt"))
    p = LlmPoller(fetch=fetch, clock=clock)
    p.poll()
    clock.now += 2.0
    fetch.body = _text("llamacpp-busy.txt")
    p.poll()
    r = p.reading()
    assert r is not None
    assert r.out_tps == pytest.approx(112.0)
    assert r.in_tps == pytest.approx(139.0)
    assert r.models == "smollm2-135m"


def test_poller_reading_goes_stale() -> None:
    clock = Clock()
    p = LlmPoller(fetch=Fetcher(_text("sglang.txt")), clock=clock)
    p.poll()
    r = p.reading()
    assert r is not None and r.kv_pct == pytest.approx(14.0) and r.requests == 1
    clock.now += 3.5
    assert p.reading() is None


def test_http_fetch_refuses_other_schemes() -> None:
    from dgx_status.llm import http_fetch

    with pytest.raises(ValueError):
        http_fetch("file:///etc/passwd", 0.3)
