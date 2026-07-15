from datetime import datetime, timezone

from contemplative_agent_otel.grouping import group_into_runs
from contemplative_agent_otel.records import LlmCall, load_records, to_epoch_ns


def _llm(ts: datetime, duration_ms: float) -> LlmCall:
    return LlmCall(
        ts=ts,
        caller="test.caller",
        model="test-model:1b",
        duration_ms=duration_ms,
        outcome="ok",
        done_reason="stop",
        prompt_eval_count=1,
        eval_count=1,
        cached_tokens=None,
        num_predict=100,
        temperature=1.0,
        think=False,
        has_format=False,
        prompt_chars=10,
        system_chars=10,
        prompt_sha256="abc",
        error_kind=None,
    )


def test_fixture_records_form_two_runs(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    runs = group_into_runs(records, gap_s=300.0)
    assert len(runs) == 2
    assert len(runs[0].records) + len(runs[1].records) == len(records)


def test_run_end_is_extended_by_llm_duration(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    runs = group_into_runs(records, gap_s=300.0)
    second = runs[1]
    # run 2's llm call starts 02:00:00 and lasts 80s; later zero-width records
    # (02:00:30, 02:01:00) must not shrink the run extent below 02:01:20.
    expected_end = to_epoch_ns(datetime(2026, 1, 1, 2, 1, 20, tzinfo=timezone.utc))
    assert second.end_ns == expected_end


def test_gap_threshold_boundary() -> None:
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    a = _llm(base, duration_ms=1000.0)  # ends at +1s
    b_within = _llm(
        datetime(2026, 1, 1, 0, 5, 1, tzinfo=timezone.utc), 100.0
    )  # exactly 300s after a ends
    runs = group_into_runs([a, b_within], gap_s=300.0)
    assert len(runs) == 1

    b_beyond = _llm(datetime(2026, 1, 1, 0, 5, 2, tzinfo=timezone.utc), 100.0)  # 301s after a ends
    runs = group_into_runs([a, b_beyond], gap_s=300.0)
    assert len(runs) == 2


def test_empty_input_yields_no_runs() -> None:
    assert group_into_runs([], gap_s=300.0) == []
