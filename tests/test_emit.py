import json

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from contemplative_agent_otel.emit import OtlpJsonFileExporter, build_provider, emit_runs
from contemplative_agent_otel.grouping import group_into_runs
from contemplative_agent_otel.records import load_records


def _provider_with_memory_exporter():
    exporter = InMemorySpanExporter()
    provider = build_provider(exporter, processor_cls=SimpleSpanProcessor)
    return provider, exporter


def _run_pipeline(fixture_dir):
    records = load_records(fixture_dir, date="2026-01-01")
    runs = group_into_runs(records, gap_s=300.0)
    provider, exporter = _provider_with_memory_exporter()
    emit_runs(runs, provider.get_tracer("test"), gap_s=300.0)
    provider.force_flush()
    provider.shutdown()
    return runs, exporter.get_finished_spans()


def test_emits_one_root_span_per_run_plus_children(fixture_dir) -> None:
    runs, spans = _run_pipeline(fixture_dir)
    roots = [s for s in spans if s.parent is None]
    children = [s for s in spans if s.parent is not None]
    assert len(roots) == 2
    assert len(children) == sum(len(r.records) for r in runs)
    assert all(r.name == "agent run" for r in roots)


def test_children_are_parented_to_their_run_root(fixture_dir) -> None:
    runs, spans = _run_pipeline(fixture_dir)
    roots = {s.context.span_id: s for s in spans if s.parent is None}
    for child in (s for s in spans if s.parent is not None):
        root = roots[child.parent.span_id]
        assert root.start_time <= child.start_time
        assert child.end_time <= root.end_time


def test_span_times_match_record_nanoseconds(fixture_dir) -> None:
    runs, spans = _run_pipeline(fixture_dir)
    reply = next(s for s in spans if s.name == "text_completion test-model:1b")
    assert reply.end_time - reply.start_time == 22_253_000_000


def test_error_records_produce_error_status(fixture_dir) -> None:
    _, spans = _run_pipeline(fixture_dir)
    errored = [s for s in spans if s.status.status_code is StatusCode.ERROR]
    assert errored, "fixture contains error records"
    assert all("error.type" in s.attributes for s in errored)


def test_resource_and_grouping_are_marked_as_reconstruction(fixture_dir) -> None:
    _, spans = _run_pipeline(fixture_dir)
    root = next(s for s in spans if s.parent is None)
    resource_attrs = root.resource.attributes
    assert resource_attrs["service.name"] == "contemplative-agent"
    assert resource_attrs["ca.convert.source"] == "offline-jsonl"
    assert root.attributes["ca.convert.grouping"] == "time-gap"
    assert root.attributes["ca.convert.gap_threshold_s"] == 300.0


def test_otlp_json_file_exporter_writes_valid_otlp_json(fixture_dir, tmp_path) -> None:
    out = tmp_path / "traces.otlp.json"
    exporter = OtlpJsonFileExporter(out)
    provider = build_provider(exporter, processor_cls=SimpleSpanProcessor)
    records = load_records(fixture_dir, date="2026-01-01")
    runs = group_into_runs(records, gap_s=300.0)
    emit_runs(runs, provider.get_tracer("test"), gap_s=300.0)
    provider.force_flush()
    provider.shutdown()

    payload = json.loads(out.read_text(encoding="utf-8"))
    resource_spans = payload["resourceSpans"]
    all_spans = [span for rs in resource_spans for ss in rs["scopeSpans"] for span in ss["spans"]]
    assert len(all_spans) == 13 + 2  # records + run roots
    rendered = json.dumps(payload)
    assert "c3ludGhldGljIGNoYWxsZW5nZTogdHdvIHBsdXMgdHdv" not in rendered
    assert "must not leak" not in rendered


def test_bulk_emission_does_not_drop_spans(fixture_dir) -> None:
    """Regression: an incident day produced 31k spans and overflowed the SDK
    default batch queue (2048), silently dropping spans. The queue must be
    sized to the conversion so every record survives."""
    from datetime import datetime, timedelta, timezone

    from contemplative_agent_otel.grouping import Run
    from contemplative_agent_otel.records import ApiCall

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = tuple(
        ApiCall(
            ts=base + timedelta(milliseconds=i),
            method="GET",
            endpoint="GET /feed",
            status=200,
            rate_remaining=None,
            success=True,
            soft_fail=None,
            drift_missing=(),
            transport_error=None,
            retried=None,
            retry_attempt=None,
            retry_max=None,
            retry_after_s=None,
            content_status={},
        )
        for i in range(5000)
    )
    run = Run(start_ns=records[0].start_ns, end_ns=records[-1].end_ns, records=records)

    exporter = InMemorySpanExporter()
    provider = build_provider(exporter, expected_spans=5001)
    emit_runs([run], provider.get_tracer("test"), gap_s=300.0)
    provider.force_flush()
    provider.shutdown()
    assert len(exporter.get_finished_spans()) == 5001


def test_run_id_grouped_run_marks_measurement_and_session(fixture_dir) -> None:
    from dataclasses import replace

    records = load_records(fixture_dir, date="2026-01-01")
    tagged = tuple(
        replace(r, run_id="run-A", session_id="sess-1") for r in records[:3]
    )
    runs = group_into_runs(tagged, gap_s=300.0)
    assert len(runs) == 1

    exporter = InMemorySpanExporter()
    provider = build_provider(exporter, processor_cls=SimpleSpanProcessor)
    emit_runs(runs, provider.get_tracer("test"), gap_s=300.0)
    provider.force_flush()
    provider.shutdown()

    root = next(s for s in exporter.get_finished_spans() if s.parent is None)
    assert root.attributes["ca.convert.grouping"] == "run-id"
    assert root.attributes["ca.audit.run_id"] == "run-A"
    assert root.attributes["session.id"] == "sess-1"
    assert "ca.convert.gap_threshold_s" not in root.attributes
