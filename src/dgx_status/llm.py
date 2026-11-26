"""LLM engine figures from the local LLM metrics exporter.

The exporter (github.com/evanwtf/llm-metrics-exporter) serves Prometheus text
at /metrics. Every engine series carries `engine`, `model`, `backend`, `host`
and `nodes`; `llme_engine_up` is 1 when the exporter read the engine on its
last attempt. Only engines that are up count.

**Polling.** A background thread fetches /metrics, waits POLL_GAP_S after
each fetch ends, and fetches again. The exporter reads every engine inside
its own scrape, so one fetch can take seconds (FETCH_TIMEOUT_S allows 10).
A frame never waits: it takes the newest reading. A failed fetch keeps the
last reading and the rate state; the reading is dropped only when it is
older than STALE_AFTER_S.

**Rates** are computed per engine, then summed. The engines' counters move
at different times (the exporter's docs/adapters.md):

- vLLM moves on each scheduler step and SGLang on a timer. The rate is the
  counter delta over one exporter update step: from the poll at which the
  counter last changed to this one. While no request runs, the base moves
  with every poll, so the first busy poll measures only busy time.
- llama.cpp and TensorFold move decode tokens only when a request finishes
  (FINISH_UPDATED). Their output rate is the decode-token delta over the
  delta of `llme_request_phase_seconds_total{phase="decode"}`: the finished
  requests' own generation time, not the wall time since the last change.
  For one request at a time that is its exact speed; for overlapping
  requests it is the speed per stream.

While requests run and the counter has not moved yet, the last measured
step's rate shows (on llama.cpp, the previous request's speed; unknown
before the first). With none running the rate is a real 0. A step longer
than MAX_STEP_S, a counter that went down, or a new or vanished series makes
it unknown.

The parser is a small one of our own (Prometheus text format 0.0.4: name,
optional labels, value, optional timestamp) rather than a new dependency:
the daemon needs a few metric names and no exposition logic.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:9109/metrics"
# Measured on the host: 141 ms to more than 5 s per fetch.
FETCH_TIMEOUT_S = 10.0
POLL_GAP_S = 1.0
# Larger than one timeout plus one gap: a reading survives one slow fetch.
STALE_AFTER_S = 15.0
MAX_STEP_S = 30.0
FAIL_LOG_EVERY_S = 60.0
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_MODELS_CHARS = 48

# The labels that name one engine (README: "Every series below carries...").
IDENTITY = ("engine", "model", "backend", "host", "nodes")
# Adapters whose decode tokens move only when a request finishes
# (docs/adapters.md, "updates: finish").
FINISH_UPDATED = frozenset({"llamacpp", "tensorfold"})

Fetch = Callable[[str, float], str]


@dataclass(frozen=True)
class PromSample:
    name: str
    labels: Mapping[str, str]
    value: float


def _parse_labels(text: str) -> dict[str, str] | None:
    """`a="x",b="y\\"z"` to a dict, or None when malformed."""
    labels: dict[str, str] = {}
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " ,":
            i += 1
        if i >= n:
            break
        eq = text.find("=", i)
        if eq < 0:
            return None
        key = text[i:eq].strip()
        q = eq + 1
        while q < n and text[q] == " ":
            q += 1
        if q >= n or text[q] != '"':
            return None
        j = q + 1
        out: list[str] = []
        while j < n and text[j] != '"':
            if text[j] == "\\" and j + 1 < n:
                nxt = text[j + 1]
                out.append({"n": "\n", "\\": "\\", '"': '"'}.get(nxt, "\\" + nxt))
                j += 2
            else:
                out.append(text[j])
                j += 1
        if j >= n or not key:
            return None
        labels[key] = "".join(out)
        i = j + 1
    return labels


def parse_prometheus(text: str) -> list[PromSample]:
    """Samples from Prometheus text; malformed lines are skipped."""
    samples = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        brace = line.find("{")
        if brace >= 0:
            close = line.rfind("}")
            if close < brace:
                continue
            name = line[:brace].strip()
            labels = _parse_labels(line[brace + 1 : close])
            rest = line[close + 1 :].split()
        else:
            parts = line.split()
            name, labels, rest = parts[0], {}, parts[1:]
        if labels is None or not rest or not name:
            continue
        try:
            value = float(rest[0])
        except ValueError:
            continue
        samples.append(PromSample(name, labels, value))
    return samples


Identity = tuple[str, ...]


def _identity(labels: Mapping[str, str]) -> Identity:
    return tuple(labels.get(k, "") for k in IDENTITY)


def _key(s: PromSample) -> str:
    """A stable series key: every label, sorted."""
    return ",".join(f"{k}={v}" for k, v in sorted(s.labels.items()))


@dataclass
class Engine:
    """One up engine's series at one poll, each keyed by its full label set,
    so a duplicated series counts once."""

    name: str
    model: str
    decode: dict[str, float] = field(default_factory=dict)
    prefill: dict[str, float] = field(default_factory=dict)
    decode_s: dict[str, float] = field(default_factory=dict)
    ttft_sum: dict[str, float] = field(default_factory=dict)
    ttft_count: dict[str, float] = field(default_factory=dict)
    spec_draft: dict[str, float] = field(default_factory=dict)
    spec_accepted: dict[str, float] = field(default_factory=dict)
    running: dict[str, float] = field(default_factory=dict)
    kv: dict[str, float] = field(default_factory=dict)

    @property
    def busy(self) -> bool:
        """Requests in flight. Unknown counts as busy: rates then hold."""
        return not self.running or sum(self.running.values()) > 0


_COUNTERS = {
    "llme_time_to_first_token_seconds_sum": "ttft_sum",
    "llme_time_to_first_token_seconds_count": "ttft_count",
    "llme_spec_draft_tokens_total": "spec_draft",
    "llme_spec_accepted_tokens_total": "spec_accepted",
    "llme_requests_running": "running",
    "llme_kv_cache_usage_ratio": "kv",
}


def reduce(samples: list[PromSample]) -> dict[Identity, Engine]:
    """The series of the engines whose llme_engine_up is 1."""
    engines: dict[Identity, Engine] = {}
    for s in samples:
        if s.name == "llme_engine_up" and s.value == 1:
            engines[_identity(s.labels)] = Engine(
                s.labels.get("engine", ""), s.labels.get("model", "")
            )
    for s in samples:
        e = engines.get(_identity(s.labels))
        if e is None or not math.isfinite(s.value):
            continue
        k = _key(s)
        phase = s.labels.get("phase")
        if s.name == "llme_tokens_total" and phase == "decode":
            e.decode[k] = s.value
        elif s.name == "llme_tokens_total" and phase == "prefill":
            e.prefill[k] = s.value
        elif s.name == "llme_request_phase_seconds_total" and phase == "decode":
            e.decode_s[k] = s.value
        elif s.name in _COUNTERS:
            getattr(e, _COUNTERS[s.name])[k] = s.value
    return engines


@dataclass(frozen=True)
class Step:
    """Counter values at one poll, with that poll's monotonic time."""

    t: float
    values: Mapping[str, float]


def _delta(prev: Step, cur: Step) -> float | None:
    """Sum of per-series deltas; None on a reset or a changed series set."""
    if set(prev.values) != set(cur.values):
        return None
    total = 0.0
    for k, v in cur.values.items():
        d = v - prev.values[k]
        if d < 0:
            return None
        total += d
    return total


class StepRate:
    """A counter's rate over its latest update step (see the module doc).

    With `clock` steps, the denominator is that counter's delta (seconds the
    engine spent) instead of the wall time between polls.
    """

    def __init__(self, max_step_s: float = MAX_STEP_S) -> None:
        self._max = max_step_s
        self._base: Step | None = None  # values at the last change
        self._clock_base: Step | None = None
        # The last measured step: what a running request shows until its
        # counter moves. Idle polls do not overwrite it.
        self._rate: float | None = None

    def update(
        self, cur: Step | None, busy: bool, clock: Step | None = None
    ) -> float | None:
        if cur is None or not cur.values:
            self._base = self._clock_base = None
            self._rate = None
            return None
        base, clock_base = self._base, self._clock_base
        if base is None:
            self._base, self._clock_base = cur, clock
            return None if busy else 0.0
        d = _delta(base, cur)
        if d is None:
            self._base, self._clock_base = cur, clock
            self._rate = None
            return None
        if d == 0:
            if not busy:
                # Idle: the base follows the polls, so the next step
                # measures busy time only.
                self._base, self._clock_base = cur, clock
                return 0.0
            # Busy, counter not moved yet: hold the last step's rate, but
            # not past the longest step we would call instant.
            return self._rate if cur.t - base.t <= self._max else None
        self._base, self._clock_base = cur, clock
        if clock is not None:
            spent = _delta(clock_base, clock) if clock_base is not None else None
            self._rate = d / spent if spent else None
        else:
            dt = cur.t - base.t
            self._rate = d / dt if 0 < dt <= self._max else None
        return self._rate


class StepRatio:
    """num delta / den delta over the latest step where den moved.

    TTFT (sum / count) and speculative acceptance (accepted / draft) are
    per-event values, not rates: the last step's value holds until the next,
    and expires MAX_STEP_S after it.
    """

    def __init__(self, max_age_s: float = MAX_STEP_S) -> None:
        self._max_age = max_age_s
        self._num: Step | None = None
        self._den: Step | None = None
        self._value: float | None = None
        self._at = -math.inf

    def update(self, num: Step | None, den: Step | None) -> float | None:
        if num is None or den is None or not den.values:
            self._num = self._den = None
            self._value = None
            return None
        if self._num is None or self._den is None:
            self._num, self._den = num, den
            return None
        dn = _delta(self._num, num)
        dd = _delta(self._den, den)
        if dn is None or dd is None:
            self._num, self._den = num, den
            self._value = None
        elif dd > 0:
            self._num, self._den = num, den
            self._value = dn / dd
            self._at = den.t
        if den.t - self._at > self._max_age:
            return None
        return self._value


class EngineRates:
    """The trackers of one engine."""

    def __init__(self) -> None:
        self.out = StepRate()
        self.inp = StepRate()
        self.ttft = StepRatio()
        self.spec = StepRatio()

    def update(
        self, e: Engine, t: float
    ) -> tuple[float | None, float | None, float | None, float | None]:
        def step(values: dict[str, float]) -> Step | None:
            return Step(t, values) if values else None

        clock = None
        if e.name in FINISH_UPDATED and e.decode_s:
            clock = Step(t, e.decode_s)
        out = self.out.update(step(e.decode), e.busy, clock)
        inp = self.inp.update(step(e.prefill), e.busy)
        ttft = self.ttft.update(step(e.ttft_sum), step(e.ttft_count))
        spec = self.spec.update(step(e.spec_accepted), step(e.spec_draft))
        return out, inp, ttft, spec


@dataclass(frozen=True)
class LlmReading:
    """One instant: None fields are unknown. engines_up 0 means idle."""

    engines_up: int
    models: str | None = None
    out_tps: float | None = None
    in_tps: float | None = None
    requests: int | None = None
    kv_pct: float | None = None
    spec_pct: float | None = None
    ttft_ms: float | None = None


def _sum(values: list[float | None]) -> float | None:
    """Sum of the engines' values; unknown if any engine's is unknown."""
    if not values or any(v is None for v in values):
        return None
    return sum(v for v in values if v is not None)


def _mean(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def check_url(url: str) -> str | None:
    """Why `url` is refused, or None. Only http(s) on loopback."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "the scheme must be http or https"
    host = parts.hostname or ""
    if host == "localhost":
        return None
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError:
        pass
    return "the host must be loopback (127.0.0.0/8, ::1 or localhost)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # urllib then raises HTTPError for the 3xx


# No proxy (an empty ProxyHandler ignores *_proxy variables) and no redirects:
# the request goes to the loopback address and nowhere else.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def http_fetch(url: str, timeout: float) -> str:
    """GET the exposition; raises OSError (or ValueError) on any failure."""
    why = check_url(url)
    if why is not None:
        raise ValueError(f"LLM exporter URL refused: {why}")
    req = urllib.request.Request(url, headers={"Accept": "text/plain"})
    with _OPENER.open(req, timeout=timeout) as resp:
        body: bytes = resp.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("exposition too large")
    return body.decode("utf-8", errors="replace")


class LlmPoller:
    """Poll the exporter and keep the newest reading.

    poll() does one fetch and is what tests drive. start() runs it on a
    daemon thread, POLL_GAP_S after each fetch ends.
    """

    def __init__(
        self,
        url: str = DEFAULT_URL,
        fetch: Fetch = http_fetch,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._reading: LlmReading | None = None
        self._read_at = -math.inf
        self._engines: dict[Identity, EngineRates] = {}
        self._failures = 0
        self._last_fail_log = -math.inf
        self._stop = threading.Event()

    def _failed(self, why: str) -> None:
        """Keep the last reading: it goes stale on its own."""
        self._failures += 1
        now = self._clock()
        if now - self._last_fail_log >= FAIL_LOG_EVERY_S:
            logger.warning(
                "LLM exporter: %s (%d failed polls since the last warning)",
                why,
                self._failures,
            )
            self._last_fail_log = now
            self._failures = 0

    def poll(self) -> None:
        try:
            text = self._fetch(self._url, FETCH_TIMEOUT_S)
            samples = parse_prometheus(text)
        except (OSError, ValueError) as exc:
            self._failed(str(exc) or type(exc).__name__)
            return
        now = self._clock()  # the sample time is when the answer arrived
        if not any(s.name == "llme_engine_up" for s in samples):
            self._failed("answer without llme_engine_up")
            return
        engines = reduce(samples)
        # Trackers follow the engines: a new engine starts fresh.
        self._engines = {i: self._engines.get(i) or EngineRates() for i in engines}
        if not engines:
            self._store(LlmReading(engines_up=0), now)
            return
        outs, ins, ttfts, specs = [], [], [], []
        running: list[float] = []
        kv: list[float] = []
        for ident, e in engines.items():
            out, inp, ttft, spec = self._engines[ident].update(e, now)
            if e.decode:
                outs.append(out)
            if e.prefill:
                ins.append(inp)
            ttfts.append(ttft)
            specs.append(spec)
            running.extend(e.running.values())
            kv.extend(e.kv.values())
        models = ", ".join(sorted({e.model for e in engines.values()} - {""}))
        self._store(
            LlmReading(
                engines_up=len(engines),
                models=models[:MAX_MODELS_CHARS] or None,
                out_tps=_sum(outs),
                in_tps=_sum(ins),
                requests=round(sum(running)) if running else None,
                kv_pct=100.0 * max(kv) if kv else None,
                spec_pct=_pct(_mean(specs)),
                ttft_ms=1000.0 * t if (t := _mean(ttfts)) is not None else None,
            ),
            now,
        )

    def _store(self, reading: LlmReading, now: float) -> None:
        with self._lock:
            self._reading = reading
            self._read_at = now

    def reading(self) -> LlmReading | None:
        """The newest reading, or None when there is none or it is stale."""
        with self._lock:
            if self._clock() - self._read_at > STALE_AFTER_S:
                return None
            return self._reading

    def start(self) -> None:
        threading.Thread(target=self._run, name="llm-poller", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # a bug here must not kill the poller
                logger.exception("LLM poll failed")
            self._stop.wait(POLL_GAP_S)


def _pct(ratio: float | None) -> float | None:
    return 100.0 * ratio if ratio is not None else None
