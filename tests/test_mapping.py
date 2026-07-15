import base64

from contemplative_agent_otel.mapping import to_span_spec
from contemplative_agent_otel.records import ApiCall, LlmCall, VerificationEvent, load_records

# The synthetic challenge body used in every fixture verification record.
_FIXTURE_CHALLENGE_TEXT = base64.b64decode("c3ludGhldGljIGNoYWxsZW5nZTogdHdvIHBsdXMgdHdv").decode(
    "utf-8"
)


def _specs(fixture_dir):
    return [to_span_spec(r) for r in load_records(fixture_dir, date="2026-01-01")]


def test_llm_ok_record_maps_to_genai_semconv(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    ok = next(r for r in records if isinstance(r, LlmCall) and r.caller == "moltbook.reply")
    spec = to_span_spec(ok)
    assert spec.name == "text_completion test-model:1b"
    assert spec.kind == "CLIENT"
    assert spec.status_error is False
    attrs = spec.attributes
    assert attrs["gen_ai.operation.name"] == "text_completion"
    assert attrs["gen_ai.provider.name"] == "ollama"
    assert attrs["gen_ai.request.model"] == "test-model:1b"
    assert attrs["gen_ai.usage.input_tokens"] == 11597
    assert attrs["gen_ai.usage.output_tokens"] == 287
    assert attrs["gen_ai.response.finish_reasons"] == ("stop",)
    assert attrs["gen_ai.request.max_tokens"] == 6717
    assert attrs["gen_ai.request.temperature"] == 1.3
    assert attrs["ca.audit.caller"] == "moltbook.reply"
    assert attrs["ca.audit.prompt_sha256"] == "aaaaaaaaaaaa"


def test_llm_error_maps_error_type_with_unknown_fallback(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    errors = [r for r in records if isinstance(r, LlmCall) and r.outcome == "error"]
    by_kind = {e.error_kind: to_span_spec(e) for e in errors}
    assert by_kind["timeout"].status_error is True
    assert by_kind["timeout"].attributes["error.type"] == "timeout"
    assert by_kind[None].status_error is True
    assert by_kind[None].attributes["error.type"] == "unknown"


def test_verification_maps_to_internal_span_with_hashes_only(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    rejected = next(
        r
        for r in records
        if isinstance(r, VerificationEvent) and r.verify_success is False and r.answer
    )
    spec = to_span_spec(rejected)
    assert spec.name == "captcha solve"
    assert spec.kind == "INTERNAL"
    assert spec.status_error is True
    assert spec.attributes["error.type"] == "http_400"
    assert spec.attributes["ca.audit.solver_path"] == "llm_extract"
    assert spec.attributes["ca.audit.challenge_sha256"] == "3" * 64


def test_api_call_maps_to_http_semconv(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    feed = next(r for r in records if isinstance(r, ApiCall) and r.endpoint == "GET /feed")
    spec = to_span_spec(feed)
    assert spec.name == "GET /feed"
    assert spec.kind == "CLIENT"
    assert spec.attributes["http.request.method"] == "GET"
    assert spec.attributes["url.template"] == "GET /feed"
    assert spec.attributes["http.response.status_code"] == 200
    assert spec.attributes["ca.audit.rate_remaining"] == 29

    transport = next(r for r in records if isinstance(r, ApiCall) and r.status is None)
    tspec = to_span_spec(transport)
    assert tspec.status_error is True
    assert tspec.attributes["error.type"] == "ConnectTimeout"


def test_attribute_values_are_otel_compatible_scalars(fixture_dir) -> None:
    allowed = (str, bool, int, float)
    for spec in _specs(fixture_dir):
        for key, value in spec.attributes.items():
            if isinstance(value, tuple):
                assert all(isinstance(v, allowed) for v in value), key
            else:
                assert isinstance(value, allowed), key
            assert value is not None, key


def test_security_no_untrusted_text_reaches_any_attribute(fixture_dir) -> None:
    """Regression gate: raw challenge bodies and server response bodies must
    never appear in any span attribute, under any key, for any record type."""
    leak_markers = [
        "c3ludGhldGljIGNoYWxsZW5nZTogdHdvIHBsdXMgdHdv",  # challenge_b64 value
        _FIXTURE_CHALLENGE_TEXT,  # decoded challenge body
        "must not leak",  # planted in every fixture free-text field
        "Incorrect answer",  # server response body fragment
        "statusCode",  # server JSON body fragment
    ]
    for spec in _specs(fixture_dir):
        for key, value in spec.attributes.items():
            rendered = " ".join(str(v) for v in value) if isinstance(value, tuple) else str(value)
            for marker in leak_markers:
                assert marker not in rendered, f"{marker!r} leaked via {key}"
        for marker in leak_markers:
            assert marker not in spec.name
