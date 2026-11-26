"""The LLM exporter reader: parser, engine selection and step rates."""

from __future__ import annotations

import http.server
import threading
import urllib.error
from pathlib import Path

import pytest

from dgx_status.llm import (
    FAIL_LOG_EVERY_S,
    FETCH_TIMEOUT_S,
    STALE_AFTER_S,
    Engine,
    LlmPoller,
    LlmReading,
    Step,
    StepRate,
    StepRatio,
    check_url,
    http_fetch,
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


def _one(name: str) -> Engine:
    engines = reduce(parse_prometheus(_text(name)))
    assert len(engines) == 1
    return next(iter(engines.values()))


def test_reduce_sglang_fixture() -> None:
    e = _one("sglang.txt")
    assert (e.name, e.model) == ("sglang", "MiMo-v2.6-Flash")
    assert sum(e.decode.values()) == 156755
    assert sum(e.prefill.values()) == 971400
    assert sum(e.running.values()) == 1 and e.busy
    assert list(e.kv.values()) == [pytest.approx(0.14)]
    assert sum(e.ttft_count.values()) == 183
    assert sum(e.ttft_sum.values()) == pytest.approx(854.3810183340029)


def test_reduce_llamacpp_busy_and_down() -> None:
    e = _one("llamacpp-busy.txt")
    assert (e.name, e.model) == ("llamacpp", "smollm2-135m")
    assert sum(e.decode.values()) == 224 and sum(e.prefill.values()) == 278
    assert sum(e.decode_s.values()) == pytest.approx(0.434104)
    assert e.kv == {}  # llama.cpp does not export it: unknown, not 0
    assert not e.busy  # requests_running is 0
    assert reduce(parse_prometheus(_text("llamacpp-down.txt"))) == {}


UP = 'llme_engine_up{engine="a",model="%s",backend="%s",host="h",nodes="1"} %d\n'
RUN = (
    'llme_requests_running{engine="a",model="%s",backend="%s",host="h",nodes="1",'
    'worker="default"} %d\n'
)


def test_series_of_down_engines_are_ignored() -> None:
    text = UP % ("m1", "b", 1) + UP % ("m2", "c", 0) + RUN % ("m1", "b", 2)
    text += RUN % ("m2", "c", 9)
    engines = reduce(parse_prometheus(text))
    assert [e.model for e in engines.values()] == ["m1"]
    assert sum(next(iter(engines.values())).running.values()) == 2


def test_a_duplicated_series_counts_once() -> None:
    text = UP % ("m1", "b", 1) + RUN % ("m1", "b", 2) + RUN % ("m1", "b", 2)
    e = next(iter(reduce(parse_prometheus(text)).values()))
    assert sum(e.running.values()) == 2


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
    assert p.reading() is None  # never read: unknown
    assert fetch.timeouts == [FETCH_TIMEOUT_S]

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


def test_a_failed_poll_keeps_the_reading_until_stale() -> None:
    clock = Clock()
    fetch = Fetcher(_text("sglang.txt"))
    p = LlmPoller(fetch=fetch, clock=clock)
    p.poll()
    first = p.reading()
    assert first is not None and first.kv_pct == pytest.approx(14.0)
    for body in (None, "<html>not metrics</html>"):
        fetch.body = body
        clock.now += 5.0
        p.poll()
        assert p.reading() == first  # kept: not wiped by one bad poll
    clock.now += STALE_AFTER_S - 10.0 + 0.1
    assert p.reading() is None  # older than STALE_AFTER_S


def test_failures_log_one_warning_per_minute(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    p = LlmPoller(fetch=Fetcher(None), clock=clock)
    with caplog.at_level("WARNING"):
        for _ in range(30):
            p.poll()
            clock.now += 1.0
        assert len(caplog.records) == 1
        clock.now += FAIL_LOG_EVERY_S
        p.poll()
    assert len(caplog.records) == 2
    assert "30 failed polls" in caplog.records[1].getMessage()


def test_poller_rates_from_two_real_expositions() -> None:
    """llama.cpp, idle then four finished requests: 224 decode tokens in
    0.434104 s of generation, 278 prefill tokens."""
    clock = Clock()
    fetch = Fetcher(_text("llamacpp-idle.txt"))
    p = LlmPoller(fetch=fetch, clock=clock)
    p.poll()
    clock.now += 2.0
    fetch.body = _text("llamacpp-busy.txt")
    p.poll()
    r = p.reading()
    assert r is not None
    assert r.out_tps == pytest.approx(224 / 0.434104)  # the requests' own speed
    assert r.in_tps == pytest.approx(139.0)
    assert r.models == "smollm2-135m"


def _expo(
    engine: str,
    decode: float,
    running: int | None,
    decode_s: float | None = None,
    model: str = "m",
) -> str:
    lab = f'engine="{engine}",model="{model}",backend="b",host="h",nodes="1"'
    w = lab + ',worker="default"'
    text = f"llme_engine_up{{{lab}}} 1\n"
    text += f'llme_tokens_total{{{w},phase="decode"}} {decode}\n'
    if running is not None:
        text += f"llme_requests_running{{{w}}} {running}\n"
    if decode_s is not None:
        text += f'llme_request_phase_seconds_total{{{w},phase="decode"}} {decode_s}\n'
    return text


def test_idle_to_busy_on_a_per_step_engine() -> None:
    """vLLM-like: 20 s idle, then 100 tok/s. The idle time is not averaged in."""
    clock = Clock()
    fetch = Fetcher(_expo("vllm", 5000, 0))
    p = LlmPoller(fetch=fetch, clock=clock)
    for _ in range(20):
        p.poll()
        r = p.reading()
        assert r is not None and r.out_tps == 0.0
        clock.now += 1.0
    tokens = 5000
    for _ in range(5):
        tokens += 100
        fetch.body = _expo("vllm", tokens, 1)
        p.poll()
        r = p.reading()
        assert r is not None and r.out_tps == pytest.approx(100.0)
        clock.now += 1.0
    fetch.body = _expo("vllm", tokens, 0)
    p.poll()
    r = p.reading()
    assert r is not None and r.out_tps == 0.0


def test_one_short_request_a_minute_on_a_finish_updated_engine() -> None:
    """llama.cpp-like: tokens move when the request ends. A 5 s request of
    500 tokens every 60 s shows 100 tok/s, its own speed, each time."""
    clock = Clock()
    tokens, seconds = 0, 0.0
    fetch = Fetcher(_expo("llamacpp", tokens, 0, seconds))
    p = LlmPoller(fetch=fetch, clock=clock)
    seen: list[float | None] = []
    for minute in range(3):
        for sec in range(60):
            if sec < 5:  # the request runs; the counters wait for its end
                fetch.body = _expo("llamacpp", tokens, 1, seconds)
            elif sec == 5:
                tokens += 500
                seconds += 5.0
                fetch.body = _expo("llamacpp", tokens, 0, seconds)
            else:
                fetch.body = _expo("llamacpp", tokens, 0, seconds)
            p.poll()
            r = p.reading()
            assert r is not None
            if sec == 5:
                seen.append(r.out_tps)
            elif sec > 5:
                assert r.out_tps == 0.0  # idle: a real zero
            elif minute > 0 and sec > 0:
                # Running, counter not moved: the last request's speed holds.
                assert r.out_tps == pytest.approx(100.0)
            clock.now += 1.0
    assert seen == [pytest.approx(100.0)] * 3


def test_mixed_engines_sum_their_own_rates() -> None:
    clock = Clock()
    v0, l0 = _expo("vllm", 0, 1, model="a"), _expo("llamacpp", 0, 1, 0.0, "b")
    fetch = Fetcher(v0 + l0)
    p = LlmPoller(fetch=fetch, clock=clock)
    p.poll()
    clock.now += 1.0
    # vLLM made 40 tokens in this second; llama.cpp finished 300 tokens
    # generated in 6 s of its own (not a 300 tok/s spike over 1 s).
    fetch.body = _expo("vllm", 40, 1, model="a") + _expo("llamacpp", 300, 1, 6.0, "b")
    p.poll()
    r = p.reading()
    assert r is not None and r.engines_up == 2 and r.requests == 2
    assert r.out_tps == pytest.approx(40.0 + 50.0)
    assert r.models == "a, b"


def test_ttft_and_acceptance_expire() -> None:
    q = StepRatio(max_age_s=30.0)
    q.update(Step(0, {"s": 0.0}), Step(0, {"c": 0}))
    assert q.update(Step(1, {"s": 0.5}), Step(1, {"c": 1})) == pytest.approx(0.5)
    assert q.update(Step(31, {"s": 0.5}), Step(31, {"c": 1})) == pytest.approx(0.5)
    assert q.update(Step(31.5, {"s": 0.5}), Step(31.5, {"c": 1})) is None


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://127.0.0.1:9109/metrics", True),
        ("http://127.5.6.7:9109/metrics", True),
        ("http://[::1]:9109/metrics", True),
        ("http://localhost:9109/metrics", True),
        ("https://localhost/metrics", True),
        ("http://10.0.0.5:9109/metrics", False),
        ("http://example.com/metrics", False),
        ("http://127.0.0.1.example.com/metrics", False),
        ("file:///etc/passwd", False),
        ("ftp://127.0.0.1/x", False),
    ],
)
def test_only_loopback_http_urls(url: str, ok: bool) -> None:
    assert (check_url(url) is None) == ok
    if not ok:
        with pytest.raises(ValueError):
            http_fetch(url, 0.3)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/metrics")
            self.end_headers()
            return
        body = b'llme_engine_up{engine="a"} 0\n'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def test_http_fetch_ignores_proxies_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        assert "llme_engine_up" in http_fetch(base + "/metrics", 2.0)
        with pytest.raises(urllib.error.HTTPError):
            http_fetch(base + "/redirect", 2.0)
    finally:
        server.shutdown()
