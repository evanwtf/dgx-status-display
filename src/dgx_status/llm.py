"""LLM engine figures from the local LLM metrics exporter.

The exporter (github.com/evanwtf/llm-metrics-exporter) serves Prometheus text
at /metrics. Every engine series carries `engine`, `model`, `backend`, `host`
and `nodes`; `llme_engine_up` is 1 when the exporter read the engine on its
last attempt. Only engines that are up count.

A background thread polls the exporter about once a second with a short
timeout, so a slow or dead exporter never delays a frame. The frame takes
the newest poll result, or nothing when it is stale.

**Rates.** The exporter re-reads the engines on each scrape, but the engines'
own counters move at different times (the exporter's docs/adapters.md): vLLM
on each scheduler step, SGLang on a timer, llama.cpp and TensorFold only
when a request finishes. A plain 1-second delta of a counter that moves
every few seconds flips between 0 and a spike. So a rate here is the counter
delta over one exporter update step: from the previous poll at which the
counter changed to this one. That is one update, never an average across
several. Between changes the last step's rate holds while requests are in
flight; with none in flight it is a real 0. A step longer than MAX_STEP_S is
too long to call instant, so that rate is unknown.

The parser is a small one of our own (Prometheus text format 0.0.4: name,
optional labels, value, optional timestamp) rather than a new dependency:
the daemon needs four metric names and no exposition logic.
"""

from __future__ import annotations

import logging
import math
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:9109/metrics"
FETCH_TIMEOUT_S = 0.3
POLL_INTERVAL_S = 1.0
# A poll result older than this is not used: the frame shows dashes.
STALE_AFTER_S = 3.0
MAX_STEP_S = 30.0
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_MODELS_CHARS = 48

# The labels that name one engine (README: "Every series below carries...").
IDENTITY = ("engine", "model", "backend", "host", "nodes")

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


def _identity(labels: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(labels.get(k, "") for k in IDENTITY)


def _key(s: PromSample) -> str:
    """A stable series key: every label, sorted."""
    return ",".join(f"{k}={v}" for k, v in sorted(s.labels.items()))


@dataclass
class Exposition:
    """What one poll of the exporter says about the engines that are up."""

    engines_up: int = 0
    models: list[str] = field(default_factory=list)
    decode: dict[str, float] = field(default_factory=dict)
    prefill: dict[str, float] = field(default_factory=dict)
    ttft_sum: dict[str, float] = field(default_factory=dict)
    ttft_count: dict[str, float] = field(default_factory=dict)
    spec_draft: dict[str, float] = field(default_factory=dict)
    spec_accepted: dict[str, float] = field(default_factory=dict)
    requests: float | None = None
    kv_ratio: float | None = None


def reduce(samples: list[PromSample]) -> Exposition:
    """Keep the series of engines whose llme_engine_up is 1."""
    up = {
        _identity(s.labels)
        for s in samples
        if s.name == "llme_engine_up" and s.value == 1
    }
    e = Exposition(engines_up=len(up))
    e.models = sorted(
        {
            s.labels.get("model", "")
            for s in samples
            if s.name == "llme_engine_up" and s.value == 1
        }
        - {""}
    )
    for s in samples:
        if _identity(s.labels) not in up or not math.isfinite(s.value):
            continue
        k = _key(s)
        if s.name == "llme_tokens_total":
            phase = s.labels.get("phase")
            if phase == "decode":
                e.decode[k] = s.value
            elif phase == "prefill":
                e.prefill[k] = s.value
        elif s.name == "llme_time_to_first_token_seconds_sum":
            e.ttft_sum[k] = s.value
        elif s.name == "llme_time_to_first_token_seconds_count":
            e.ttft_count[k] = s.value
        elif s.name == "llme_spec_draft_tokens_total":
            e.spec_draft[k] = s.value
        elif s.name == "llme_spec_accepted_tokens_total":
            e.spec_accepted[k] = s.value
        elif s.name == "llme_requests_running":
            e.requests = (e.requests or 0.0) + s.value
        elif s.name == "llme_kv_cache_usage_ratio":
            e.kv_ratio = s.value if e.kv_ratio is None else max(e.kv_ratio, s.value)
    return e


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
    """A counter's rate over its latest update step (see the module doc)."""

    def __init__(self, max_step_s: float = MAX_STEP_S) -> None:
        self._max = max_step_s
        self._base: Step | None = None  # values at the last change
        self._rate: float | None = None

    def update(self, cur: Step | None, busy: bool) -> float | None:
        if cur is None or not cur.values:
            self._base = None
            self._rate = None
            return None
        base = self._base
        if base is None:
            self._base = cur
            return None if busy else 0.0
        d = _delta(base, cur)
        if d is None:
            self._base = cur
            self._rate = None
            return None
        if d == 0:
            if not busy:
                self._rate = 0.0
                return 0.0
            # Busy, counter not moved yet: hold the last step's rate, but
            # not past the longest step we would call instant.
            return self._rate if cur.t - base.t <= self._max else None
        dt = cur.t - base.t
        self._base = cur
        self._rate = d / dt if 0 < dt <= self._max else None
        return self._rate


class StepRatio:
    """num delta / den delta over the latest step where den moved.

    TTFT (sum / count) and speculative acceptance (accepted / draft) are
    per-event values, not rates: the last step's value holds until the next.
    """

    def __init__(self) -> None:
        self._num: Step | None = None
        self._den: Step | None = None
        self.value: float | None = None

    def update(self, num: Step | None, den: Step | None) -> float | None:
        if num is None or den is None or not den.values:
            self._num = self._den = None
            self.value = None
            return None
        if self._num is None or self._den is None:
            self._num, self._den = num, den
            return self.value
        dn = _delta(self._num, num)
        dd = _delta(self._den, den)
        if dn is None or dd is None:
            self._num, self._den = num, den
            self.value = None
        elif dd > 0:
            self._num, self._den = num, den
            self.value = dn / dd
        return self.value


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


def http_fetch(url: str, timeout: float) -> str:
    """GET the exposition; raises OSError (or ValueError) on any failure."""
    if not url.startswith(("http://", "https://")):
        # urlopen also reads file:// and ftp://; the exporter is HTTP.
        raise ValueError("LLM exporter URL must be http:// or https://")
    req = urllib.request.Request(url, headers={"Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body: bytes = resp.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("exposition too large")
    return body.decode("utf-8", errors="replace")


class LlmPoller:
    """Poll the exporter and keep the newest reading.

    poll() does one fetch and is what tests drive. start() runs it on a
    daemon thread every POLL_INTERVAL_S.
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
        self._out = StepRate()
        self._in = StepRate()
        self._ttft = StepRatio()
        self._spec = StepRatio()
        self._failing = False
        self._stop = threading.Event()

    def poll(self) -> None:
        now = self._clock()
        try:
            text = self._fetch(self._url, FETCH_TIMEOUT_S)
            samples = parse_prometheus(text)
        except (OSError, ValueError) as exc:
            if not self._failing:
                logger.warning("LLM exporter unreachable: %s", exc)
            self._failing = True
            self._reset(None)
            return
        if not any(s.name == "llme_engine_up" for s in samples):
            if not self._failing:
                logger.warning("LLM exporter answered without llme_engine_up")
            self._failing = True
            self._reset(None)
            return
        if self._failing:
            logger.info("LLM exporter readable again")
        self._failing = False
        e = reduce(samples)
        if e.engines_up == 0:
            self._reset(LlmReading(engines_up=0), now)
            return
        busy = e.requests is None or e.requests > 0
        out = self._out.update(Step(now, e.decode) if e.decode else None, busy)
        inp = self._in.update(Step(now, e.prefill) if e.prefill else None, busy)
        ttft = self._ttft.update(
            Step(now, e.ttft_sum) if e.ttft_sum else None,
            Step(now, e.ttft_count) if e.ttft_count else None,
        )
        spec = self._spec.update(
            Step(now, e.spec_accepted) if e.spec_accepted else None,
            Step(now, e.spec_draft) if e.spec_draft else None,
        )
        models = ", ".join(e.models)[:MAX_MODELS_CHARS] or None
        reading = LlmReading(
            engines_up=e.engines_up,
            models=models,
            out_tps=out,
            in_tps=inp,
            requests=round(e.requests) if e.requests is not None else None,
            kv_pct=100.0 * e.kv_ratio if e.kv_ratio is not None else None,
            spec_pct=100.0 * spec if spec is not None else None,
            ttft_ms=1000.0 * ttft if ttft is not None else None,
        )
        with self._lock:
            self._reading = reading
            self._read_at = now

    def _reset(self, reading: LlmReading | None, now: float | None = None) -> None:
        self._out = StepRate()
        self._in = StepRate()
        self._ttft = StepRatio()
        self._spec = StepRatio()
        with self._lock:
            self._reading = reading
            self._read_at = now if now is not None else -math.inf

    def reading(self) -> LlmReading | None:
        """The newest reading, or None when the exporter is down or stale."""
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
            started = self._clock()
            try:
                self.poll()
            except Exception:  # a bug here must not kill the poller
                logger.exception("LLM poll failed")
            self._stop.wait(max(0.0, POLL_INTERVAL_S - (self._clock() - started)))
