"""Map typed audit records to OTel span specifications.

Attribute names are hand-pinned string constants rather than imports from
``opentelemetry-semantic-conventions``: the GenAI conventions are still
Development-status and the package exposes them only under an unstable
``_incubating`` path. Pinned against:
open-telemetry/semantic-conventions-genai, docs/gen-ai/gen-ai-spans.md
(as of 2026-07, status: Development).

The canonical human-readable field-by-field table lives in docs/mapping.md;
tests/test_mapping.py asserts this module and that table stay in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Union

from .records import ApiCall, LlmCall, Record, VerificationEvent

# GenAI semantic conventions (Development) — gen-ai-spans.md
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

# HTTP semantic conventions (stable) — used for the Moltbook API audit log
HTTP_REQUEST_METHOD = "http.request.method"
HTTP_RESPONSE_STATUS_CODE = "http.response.status_code"
URL_TEMPLATE = "url.template"

ERROR_TYPE = "error.type"

# Custom namespace for fields with no semconv equivalent. These are the
# research-replay-specific fields — their very lack of a standard mapping
# is a finding about what ADR-0075 logs that ops telemetry does not.
_CA = "ca.audit."

AttrValue = Union[str, bool, int, float, tuple]


@dataclass(frozen=True)
class SpanSpec:
    name: str
    kind: str  # "CLIENT" | "INTERNAL"
    start_ns: int
    end_ns: int
    attributes: Mapping[str, AttrValue]
    status_error: bool
    error_type: Optional[str]


def _put(attrs: dict[str, AttrValue], key: str, value: object) -> None:
    """Set an attribute only when the value is a usable OTel scalar."""
    if value is None:
        return
    if isinstance(value, (str, bool, int, float, tuple)):
        attrs[key] = value


def _llm_spec(record: LlmCall) -> SpanSpec:
    model = record.model or "unknown"
    attrs: dict[str, AttrValue] = {
        GEN_AI_OPERATION_NAME: "text_completion",  # Ollama /api/generate, not chat
        GEN_AI_PROVIDER_NAME: "ollama",
    }
    _put(attrs, GEN_AI_REQUEST_MODEL, record.model)
    _put(attrs, GEN_AI_REQUEST_MAX_TOKENS, record.num_predict)
    _put(attrs, GEN_AI_REQUEST_TEMPERATURE, record.temperature)
    _put(attrs, GEN_AI_USAGE_INPUT_TOKENS, record.prompt_eval_count)
    _put(attrs, GEN_AI_USAGE_OUTPUT_TOKENS, record.eval_count)
    if record.done_reason is not None:
        attrs[GEN_AI_RESPONSE_FINISH_REASONS] = (record.done_reason,)

    _put(attrs, _CA + "caller", record.caller)
    _put(attrs, _CA + "outcome", record.outcome)
    _put(attrs, _CA + "think", record.think)
    _put(attrs, _CA + "has_format", record.has_format)
    _put(attrs, _CA + "cached_tokens", record.cached_tokens)
    _put(attrs, _CA + "prompt_sha256", record.prompt_sha256)
    _put(attrs, _CA + "prompt_chars", record.prompt_chars)
    _put(attrs, _CA + "system_chars", record.system_chars)

    is_error = record.outcome == "error"
    error_type: Optional[str] = None
    if is_error:
        error_type = record.error_kind or "unknown"
        attrs[ERROR_TYPE] = error_type

    return SpanSpec(
        name=f"text_completion {model}",
        kind="CLIENT",
        start_ns=record.start_ns,
        end_ns=record.end_ns,
        attributes=attrs,
        status_error=is_error,
        error_type=error_type,
    )


def _verification_spec(record: VerificationEvent) -> SpanSpec:
    attrs: dict[str, AttrValue] = {}
    # Hashes and flags only — the challenge body (attacker-controlled text)
    # was already dropped at parse time and has no path into attributes.
    _put(attrs, _CA + "challenge_sha256", record.challenge_sha256)
    _put(attrs, _CA + "challenge_bytes", record.challenge_bytes)
    _put(attrs, _CA + "challenge_truncated", record.challenge_truncated)
    _put(attrs, _CA + "challenge_encoding", record.challenge_encoding)
    _put(attrs, _CA + "verification_code_sha256", record.verification_code_sha256)
    _put(attrs, _CA + "answer", record.answer)
    _put(attrs, _CA + "solver_path", record.solver_path)
    _put(attrs, _CA + "solve_success", record.solve_success)
    _put(attrs, _CA + "verify_success", record.verify_success)

    is_error = record.verify_success is False
    error_type: Optional[str] = None
    if is_error:
        error_type = record.error_status or "verify_rejected"
        attrs[ERROR_TYPE] = error_type

    return SpanSpec(
        name="captcha solve",
        kind="INTERNAL",
        start_ns=record.start_ns,
        end_ns=record.end_ns,
        attributes=attrs,
        status_error=is_error,
        error_type=error_type,
    )


def _api_spec(record: ApiCall) -> SpanSpec:
    attrs: dict[str, AttrValue] = {}
    _put(attrs, HTTP_REQUEST_METHOD, record.method)
    _put(attrs, URL_TEMPLATE, record.endpoint)
    _put(attrs, HTTP_RESPONSE_STATUS_CODE, record.status)
    _put(attrs, _CA + "rate_remaining", record.rate_remaining)
    _put(attrs, _CA + "success", record.success)
    _put(attrs, _CA + "soft_fail", record.soft_fail)
    _put(attrs, _CA + "retried", record.retried)
    _put(attrs, _CA + "retry_attempt", record.retry_attempt)
    _put(attrs, _CA + "retry_max", record.retry_max)
    _put(attrs, _CA + "retry_after_s", record.retry_after_s)
    if record.drift_missing:
        attrs[_CA + "drift_missing"] = record.drift_missing
    for key, value in sorted(record.content_status.items()):
        _put(attrs, f"{_CA}content_status.{key}", value)

    is_error = record.transport_error is not None or record.success is False
    error_type: Optional[str] = None
    if is_error:
        if record.transport_error is not None:
            error_type = record.transport_error
        elif record.status is not None:
            error_type = str(record.status)
        else:
            error_type = "unknown"
        attrs[ERROR_TYPE] = error_type

    return SpanSpec(
        name=record.endpoint or f"{record.method or 'CALL'} unknown",
        kind="CLIENT",
        start_ns=record.start_ns,
        end_ns=record.end_ns,
        attributes=attrs,
        status_error=is_error,
        error_type=error_type,
    )


def to_span_spec(record: Record) -> SpanSpec:
    if isinstance(record, LlmCall):
        return _llm_spec(record)
    if isinstance(record, VerificationEvent):
        return _verification_spec(record)
    if isinstance(record, ApiCall):
        return _api_spec(record)
    raise TypeError(f"unsupported record type: {type(record)!r}")
