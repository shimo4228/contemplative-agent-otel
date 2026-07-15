from datetime import datetime, timezone

from contemplative_agent_otel.records import (
    ApiCall,
    LlmCall,
    VerificationEvent,
    load_records,
    to_epoch_ns,
)


def test_to_epoch_ns_known_value() -> None:
    dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert to_epoch_ns(dt) == 1767225600_000_000_000


def test_load_records_merges_and_sorts_by_ts(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    assert len(records) == 13  # 5 llm (1 broken line skipped) + 3 verification + 5 api
    starts = [r.start_ns for r in records]
    assert starts == sorted(starts)


def test_broken_and_blank_lines_are_skipped_not_fatal(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    llm = [r for r in records if isinstance(r, LlmCall)]
    assert len(llm) == 5


def test_llm_call_span_extent_is_ts_plus_duration(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    first = [r for r in records if isinstance(r, LlmCall)][0]
    assert first.caller == "moltbook.reply"
    assert first.end_ns - first.start_ns == 22_253_000_000  # 22253 ms in ns


def test_llm_error_without_error_kind_is_tolerated(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    errors = [r for r in records if isinstance(r, LlmCall) and r.outcome == "error"]
    kinds = sorted(str(e.error_kind) for e in errors)
    assert kinds == ["None", "timeout"]


def test_zero_width_records_have_equal_start_and_end(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    for r in records:
        if isinstance(r, (VerificationEvent, ApiCall)):
            assert r.start_ns == r.end_ns


def test_verification_never_retains_challenge_body(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    events = [r for r in records if isinstance(r, VerificationEvent)]
    assert events, "fixture must contain verification events"
    for event in events:
        assert not hasattr(event, "challenge_b64")


def test_verification_error_body_is_reduced_to_status_class(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    events = [r for r in records if isinstance(r, VerificationEvent)]
    rejected = [e for e in events if e.verify_success is False and e.answer is not None]
    assert rejected[0].error_status == "http_400"
    abstained = [e for e in events if e.solver_path == "none"]
    assert abstained[0].error_status == "abstain"


def test_api_call_untrusted_text_fields_are_dropped(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    calls = [r for r in records if isinstance(r, ApiCall)]
    assert len(calls) == 5
    for call in calls:
        assert not hasattr(call, "error")
        assert not hasattr(call, "keys")


def test_api_call_content_status_keeps_only_scalars(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    soft = [r for r in records if isinstance(r, ApiCall) and r.soft_fail]
    assert soft[0].content_status == {"is_spam": False, "requires_review": True}
