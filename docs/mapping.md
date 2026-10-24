# Audit-log → OTel semantic-conventions mapping

> **Frozen with this package (as of contemplative-agent v2.13.0).** The canonical
> mapping now lives in the main repository's
> [`docs/otel-semconv-mapping.md`](https://github.com/shimo4228/contemplative-agent/blob/main/docs/otel-semconv-mapping.md),
> which also covers the embedding and decision row kinds. This copy documents
> what the frozen converter does (the v2.10.0 schema).

Field-by-field mapping from contemplative-agent's replayable audit
JSONL logs ([ADR-0075](https://github.com/shimo4228/contemplative-agent/blob/main/docs/adr/0075-observability-by-default.md))
to OpenTelemetry span attributes. `tests/test_mapping.py` asserts that
`src/contemplative_agent_otel/mapping.py` and this table stay in sync.

> **Semconv status note.** The GenAI semantic conventions are
> **Development** status (not stable). Attribute names below are pinned
> against [open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai)
> `docs/gen-ai/gen-ai-spans.md` as of 2026-07. They are hand-written string
> constants in `mapping.py` — the `opentelemetry-semantic-conventions`
> package only exposes them under an unstable `_incubating` import path,
> which is not a dependency you want.

## `llm-calls-{date}.jsonl` → span `text_completion {model}` (CLIENT)

Written by `core/llm.py:_emit_telemetry` in the main repository. One span
per LLM call; `start = ts`, `end = ts + duration_ms`.

| JSONL field | OTel attribute | Note |
|---|---|---|
| — | `gen_ai.operation.name` = `"text_completion"` | Ollama `/api/generate`, not chat |
| — | `gen_ai.provider.name` = `"ollama"` | |
| `model` | `gen_ai.request.model` | |
| `prompt_eval_count` | `gen_ai.usage.input_tokens` | |
| `eval_count` | `gen_ai.usage.output_tokens` | |
| `done_reason` | `gen_ai.response.finish_reasons` | single-element array |
| `num_predict` | `gen_ai.request.max_tokens` | |
| `temperature` | `gen_ai.request.temperature` | |
| `error_kind` (when `outcome == "error"`) | `error.type` | falls back to `"unknown"` — error records without `error_kind` exist (e.g. circuit-open short-circuits) |
| `duration_ms` | span duration | |
| `caller` | `ca.audit.caller` | no semconv equivalent — the replay-oriented stage label |
| `outcome` | `ca.audit.outcome` | |
| `think` | `ca.audit.think` | |
| `has_format` | `ca.audit.has_format` | |
| `cached_tokens` | `ca.audit.cached_tokens` | |
| `prompt_sha256` | `ca.audit.prompt_sha256` | content key without content — prompt bodies are never logged |
| `prompt_chars` / `system_chars` | `ca.audit.prompt_chars` / `ca.audit.system_chars` | |

## `verification-audit.jsonl` → span `captcha solve` (INTERNAL, zero-width)

The log records no duration, so the span is **zero-width** (`end == start`).
An estimated width would be fabricated latency; the honest conversion keeps
it at zero — and "logs without a duration become zero-width spans" is
itself feedback for the audit-record schema.

| JSONL field | OTel attribute | Note |
|---|---|---|
| `challenge_sha256` | `ca.audit.challenge_sha256` | |
| `challenge_bytes` | `ca.audit.challenge_bytes` | |
| `challenge_truncated` | `ca.audit.challenge_truncated` | |
| `challenge_encoding` | `ca.audit.challenge_encoding` | |
| `verification_code_sha256` | `ca.audit.verification_code_sha256` | |
| `answer` | `ca.audit.answer` | agent-generated two-decimal string |
| `solver_path` | `ca.audit.solver_path` | `code_parse` / `llm_extract` / `llm_reason` / `none` |
| `solve_success` / `verify_success` | `ca.audit.solve_success` / `ca.audit.verify_success` | |
| `error` (when `verify_success == false`) | `error.type` | **classification only** (`http_400`, `abstain`, …); the body is dropped |

## `api-audit.jsonl` → span `{method} {endpoint}` (CLIENT, zero-width)

| JSONL field | OTel attribute | Note |
|---|---|---|
| `method` | `http.request.method` | stable HTTP semconv |
| `endpoint` | `url.template` | already normalized (`GET /posts/{id}`) |
| `status` | `http.response.status_code` | |
| `transport_error` / `status` | `error.type` | exception class name, or status code as string |
| `rate_remaining` | `ca.audit.rate_remaining` | |
| `success` / `soft_fail` | `ca.audit.success` / `ca.audit.soft_fail` | |
| `retried` / `retry_attempt` / `retry_max` / `retry_after_s` | `ca.audit.retried` / … | 429 retry decisions |
| `drift_missing` | `ca.audit.drift_missing` | schema-drift detection (missing envelope keys) |
| `content_status.*` | `ca.audit.content_status.*` | **scalars only** (bool/int/float); server strings are dropped |

## Excluded fields — and why

| Field | Reason |
|---|---|
| `challenge_b64` (verification) | Attacker-controlled text. The audit log stores it (base64 + sha256) because offline replay *needs* the raw input; a trace viewer does not, and rendering it in a UI (or a screenshot) would move untrusted text across a trust boundary. There is deliberately **no flag** to include it — the parser never retains it. |
| `error` bodies (verification, api) | Server-controlled free text (contains full response JSON). Reduced to a categorical `error.type` at parse time. |
| `keys` (api) | Response envelope key listing; low signal for traces (drift detection already surfaces as `drift_missing`). |
| string values inside `content_status` | Server-controlled free text. |

This exclusion list is enforced by `tests/test_mapping.py::test_security_no_untrusted_text_reaches_any_attribute`
and mirrors the split the two systems are built for: ADR-0075 logs exist to
**replay decisions offline** (raw inputs required, stored injection-safe);
OTel traces exist to **observe operations** (content redacted by default,
per the GenAI conventions). Same events, different consumers, opposite
retention choices.

## Fields with no OTLP mapping at all

`distill` partial-failure summaries in the main repository are aggregated
`logger.warning` lines, not JSONL records — they have no per-event structure
to convert and are out of scope here.

## Execution identity (all three logs)

Since 2026-07-16 the main repository stamps every audit record in its shared
writer (`append_jsonl_restricted`) with:

| field | OTel mapping | meaning |
|---|---|---|
| `run_id` | trace grouping key; surfaced as `ca.audit.run_id` on the root span | one per process execution (any CLI command) |
| `session_id` | `session.id` (general semconv) on the root span | present only while an autonomous agent session (`run`) is active |

## Trace grouping

Preferred: group by `run_id` — that is measurement (`ca.convert.grouping =
"run-id"`). Records without the field (logs written before it existed) fall
back to time-gap clustering (default 300 s of silence starts a new
`agent run` root span), which matches the agent's launchd cadence but is
inference, not ground truth — those roots carry `ca.convert.grouping =
"time-gap"` and `ca.convert.gap_threshold_s`. A mixed corpus yields both
kinds side by side, so a reader can always tell reconstruction from
measurement.
