"""OTel SDK glue: turn Runs of SpanSpecs into real spans with past timestamps.

The SDK accepts explicit ``start_time`` / ``end_time`` in epoch nanoseconds,
which is what makes offline conversion possible at all — spans are emitted
"as of" when the audit log recorded them, not when the converter ran.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer, set_span_in_context

from . import __version__
from .grouping import Run
from .mapping import to_span_spec

_KINDS = {
    "CLIENT": SpanKind.CLIENT,
    "INTERNAL": SpanKind.INTERNAL,
}


def build_provider(
    exporter: SpanExporter,
    processor_cls: type = BatchSpanProcessor,
    expected_spans: int | None = None,
) -> TracerProvider:
    """Create a local (non-global) provider marked as an offline conversion.

    ``expected_spans`` sizes the batch queue for offline bulk emission. The
    SDK default queue (2048) silently drops spans when a converted day
    outpaces the exporter — an incident day here produced 31k spans and
    dropped some. An offline converter must never drop records silently,
    so the queue is sized to hold the whole conversion.
    """
    resource = Resource.create(
        {
            "service.name": "contemplative-agent",
            "ca.convert.source": "offline-jsonl",
            "ca.convert.version": __version__,
        }
    )
    provider = TracerProvider(resource=resource)
    if processor_cls is BatchSpanProcessor and expected_spans is not None:
        queue_size = max(2048, expected_spans + 1024)
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=queue_size,
            max_export_batch_size=512,
        )
    else:
        processor = processor_cls(exporter)
    provider.add_span_processor(processor)
    return provider


def emit_runs(runs: Sequence[Run], tracer: Tracer, gap_s: float) -> int:
    """Emit one 'agent run' root span per run, with records as flat children.

    Returns the total number of spans emitted (roots + children).
    """
    emitted = 0
    for run in runs:
        root = tracer.start_span(
            "agent run",
            kind=SpanKind.INTERNAL,
            start_time=run.start_ns,
            attributes={
                # Grouping is a reconstruction (no run id in the logs) — say so.
                "ca.convert.grouping": "time-gap",
                "ca.convert.gap_threshold_s": gap_s,
                "ca.convert.record_count": len(run.records),
            },
        )
        parent_ctx = set_span_in_context(root)
        for record in run.records:
            spec = to_span_spec(record)
            span = tracer.start_span(
                spec.name,
                context=parent_ctx,
                kind=_KINDS[spec.kind],
                start_time=spec.start_ns,
                attributes=dict(spec.attributes),
            )
            if spec.status_error:
                span.set_status(Status(StatusCode.ERROR, spec.error_type))
            span.end(end_time=spec.end_ns)
            emitted += 1
        root.end(end_time=run.end_ns)
        emitted += 1
    return emitted


class OtlpJsonFileExporter(SpanExporter):
    """Collect spans and write a single OTLP/JSON document on shutdown.

    The output is the JSON encoding of ``ExportTraceServiceRequest`` — the
    same payload an OTLP/HTTP exporter would POST to ``/v1/traces`` — so
    readers can inspect exactly what a collector would have received.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self) -> None:
        from google.protobuf.json_format import MessageToJson

        request = encode_spans(self._spans)
        self._path.write_text(MessageToJson(request), encoding="utf-8")


def make_otlp_http_exporter(endpoint: str) -> SpanExporter:
    """Build the OTLP/HTTP exporter (imported lazily to keep tests offline)."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")


def summarize(runs: Sequence[Run]) -> dict[str, object]:
    """Dry-run summary: what would be emitted, without emitting."""
    by_type: dict[str, int] = {}
    for run in runs:
        for record in run.records:
            name = type(record).__name__
            by_type[name] = by_type.get(name, 0) + 1
    return {
        "runs": len(runs),
        "records": sum(len(r.records) for r in runs),
        "spans": sum(len(r.records) for r in runs) + len(runs),
        "by_type": by_type,
    }


__all__ = [
    "OtlpJsonFileExporter",
    "build_provider",
    "emit_runs",
    "make_otlp_http_exporter",
    "summarize",
]
