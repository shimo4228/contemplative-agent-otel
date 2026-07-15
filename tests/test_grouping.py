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


def _llm_with_ids(ts, run_id, session_id=None):
    from dataclasses import replace

    base = _llm(ts, duration_ms=1000.0)
    return replace(base, run_id=run_id, session_id=session_id)


def test_records_with_run_id_group_by_id_not_time() -> None:
    from datetime import datetime, timezone

    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)  # 3h gap, same run_id
    a = _llm_with_ids(base, run_id="run-A")
    b = _llm_with_ids(later, run_id="run-A")
    runs = group_into_runs([a, b], gap_s=300.0)
    assert len(runs) == 1
    assert runs[0].grouping == "run-id"


def test_distinct_run_ids_split_even_when_adjacent() -> None:
    from datetime import datetime, timezone

    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    close = datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)  # 2s apart, different run_id
    runs = group_into_runs(
        [_llm_with_ids(base, "run-A"), _llm_with_ids(close, "run-B")], gap_s=300.0
    )
    assert len(runs) == 2


def test_mixed_corpus_uses_id_and_time_gap_side_by_side(fixture_dir) -> None:
    from datetime import datetime, timezone

    legacy = load_records(fixture_dir, date="2026-01-01")  # run_id なしの既存 fixture
    tagged = _llm_with_ids(datetime(2026, 1, 2, tzinfo=timezone.utc), "run-A")
    runs = group_into_runs([*legacy, tagged], gap_s=300.0)
    groupings = sorted(r.grouping for r in runs)
    assert groupings == ["run-id", "time-gap", "time-gap"]
    starts = [r.start_ns for r in runs]
    assert starts == sorted(starts)


def test_time_gap_runs_report_their_grouping(fixture_dir) -> None:
    records = load_records(fixture_dir, date="2026-01-01")
    runs = group_into_runs(records, gap_s=300.0)
    assert all(r.grouping == "time-gap" for r in runs)


def test_empty_string_run_id_falls_back_to_time_gap(fixture_dir) -> None:
    """回帰 (python-reviewer 2026-07-16): run_id="" が合成 run に併合され、
    かつ emit 側の truthy 判定と食い違って無ラベル span になる"""
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    far = base + timedelta(hours=2)
    a = _llm_with_ids(base, run_id="")
    b = _llm_with_ids(far, run_id="")
    runs = group_into_runs([a, b], gap_s=300.0)
    assert len(runs) == 2  # "" は id として扱わない → 時間ギャップで分割
    assert all(r.grouping == "time-gap" for r in runs)


def test_parser_normalizes_empty_id_to_none(tmp_path) -> None:
    import json

    from contemplative_agent_otel.records import load_records as _load

    line = {
        "ts": "2026-01-01T00:00:00+00:00",
        "caller": "t",
        "model": "m",
        "duration_ms": 1,
        "outcome": "ok",
        "run_id": "",
        "session_id": "",
    }
    (tmp_path / "llm-calls-2026-01-01.jsonl").write_text(
        json.dumps(line) + "\n", encoding="utf-8"
    )
    records = _load(tmp_path, date="2026-01-01")
    assert records[0].run_id is None
    assert records[0].session_id is None
