# LLM metrics exporter fixtures

Real `/metrics` bodies from llm-metrics-exporter 0.1.0
(github.com/evanwtf/llm-metrics-exporter at `e9a0339`), made on 2026-11-26
by running the exporter against its own engine testdata, served from a
local HTTP server. `serve --host test-host`, so no host label names a
machine.

| File | Engine input (exporter `testdata/`) | Shows |
| --- | --- | --- |
| `llamacpp-idle.txt` | `llamacpp/b10809-5266f24da-idle.metrics.txt` | an engine up, every counter 0 |
| `llamacpp-busy.txt` | `llamacpp/b10809-5266f24da.metrics.txt` | the same engine after four requests |
| `llamacpp-down.txt` | none: the engine stopped | `llme_engine_up 0` |
| `sglang.txt` | `sglang/nightly-dev-cu13-20260921-0f6761b5.metrics.txt` | a two-node engine mid-benchmark: KV cache, TTFT histogram, one request running |
