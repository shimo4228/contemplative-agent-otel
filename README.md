# contemplative-agent-otel

Offline converter from [contemplative-agent](https://github.com/shimo4228/contemplative-agent)'s
replayable audit JSONL logs to OpenTelemetry OTLP traces, using the
[GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai).

```
audit JSONL (MOLTBOOK_HOME/logs/)  ──►  OTLP/HTTP :4318  ──►  Jaeger UI
llm-calls / verification / api          (or --to-file JSON)
```

The main repository deliberately does **not** ship OpenTelemetry at runtime
([ADR-0075](https://github.com/shimo4228/contemplative-agent/blob/main/docs/adr/0075-observability-by-default.md):
single local process, `requests`+`numpy` dependency floor, and a
research-replay requirement that operational tracing does not serve). This
sibling connects those logs to the OTel ecosystem **after the fact**:

- **Zero runtime coupling.** No instrumentation, no import of
  `contemplative-agent` — the converter reads log files only.
- **Zero main-repo changes.** Same pattern as the
  [cloud](https://github.com/shimo4228/contemplative-agent-cloud) /
  [mlx](https://github.com/shimo4228/contemplative-agent-mlx) siblings.
- **Untrusted content stays out of the trace.** Challenge bodies and server
  response bodies are dropped at parse time (hashes and categorical codes
  survive); a regression test enforces it. See
  [docs/mapping.md](docs/mapping.md) for the full field-by-field table and
  exclusion rationale.

## Usage

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e .

# summary only — what would be emitted
contemplative-agent-otel --date 2026-07-15 --dry-run

# emit to a local OTLP endpoint (Jaeger, otel-desktop-viewer, any collector)
contemplative-agent-otel --date 2026-07-15

# or write OTLP/JSON to a file (no viewer needed)
contemplative-agent-otel --date 2026-07-15 --to-file traces.otlp.json
```

Options: `--log-dir` (default `~/.config/moltbook/logs`), `--date YYYY-MM-DD`
(default: all days), `--gap-seconds` (default 300 — silence gap that starts a
new `agent run` trace), `--endpoint` (default `http://localhost:4318`).

## Viewing with Jaeger v2 (no Docker)

Jaeger v2 is a single native binary that accepts OTLP out of the box and
stores traces in memory:

```bash
# https://www.jaegertracing.io/download/  (darwin-arm64 tarball)
tar xzf jaeger-2.x.y-darwin-arm64.tar.gz
./jaeger                                   # OTLP :4317/:4318, UI :16686
contemplative-agent-otel --date 2026-07-15
open http://localhost:16686
```

Alternative: [otel-desktop-viewer](https://github.com/CtrlSpice/otel-desktop-viewer)
also works unchanged (it listens on the same OTLP ports).

### Screenshots (real audit logs, converted offline)

A normal day — one `agent run` root span, LLM calls with real durations,
zero-width API/verification markers:

![agent run waterfall](docs/images/jaeger-run-waterfall.png)

A `text_completion` span with GenAI semconv attributes (`gen_ai.usage.*`,
`gen_ai.response.finish_reasons`) alongside the replay-specific
`ca.audit.*` namespace:

![span detail with gen_ai attributes](docs/images/jaeger-span-genai.png)

An incident day is visible at a glance — one run ballooned to 30,442 spans
with 33 errors (a retry storm the audit logs had already recorded):

![incident day search](docs/images/jaeger-search-incident.png)

A failed CAPTCHA verification shows up as paired error spans
(`captcha solve` + `POST /verify`), with the server's response body reduced
to `error.type` — the untrusted text never reaches the viewer:

![error run](docs/images/jaeger-error-run.png)

## What a trace looks like

Each `agent run` root span groups the records of one process execution —
by the `run_id` the logs carry (stamped by the main repo's shared audit
writer since 2026-07-16; `session.id` is added while an agent session is
active), falling back to time-gap clustering for older records without the
field. The root span's `ca.convert.grouping` attribute says which kind you
are looking at. Child spans:

| span | source log | semconv |
|---|---|---|
| `text_completion {model}` | `llm-calls-{date}.jsonl` | `gen_ai.*` (GenAI, Development status) |
| `GET /feed`, `POST /comments`, … | `api-audit.jsonl` | `http.*` / `url.template` |
| `captcha solve` | `verification-audit.jsonl` | `ca.audit.*` + `error.type` |

LLM spans carry real durations (`ts` + `duration_ms`); verification and API
records carry no duration in the log and become zero-width markers — an
honest artifact of converting audit records rather than instrumenting.

## Tests

```bash
uv pip install -e ".[dev]"
pytest
```

All tests run offline against synthetic fixtures. The security regression
test walks every produced span attribute and asserts that no untrusted text
(challenge bodies, server response bodies) survives the conversion.

## License

MIT
