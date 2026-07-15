"""CLI entry point: convert audit JSONL logs to OTLP traces."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from .emit import (
    OtlpJsonFileExporter,
    build_provider,
    emit_runs,
    make_otlp_http_exporter,
    summarize,
)
from .grouping import group_into_runs
from .records import load_records

_DEFAULT_LOG_DIR = Path.home() / ".config" / "moltbook" / "logs"
_DEFAULT_ENDPOINT = "http://localhost:4318"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contemplative-agent-otel",
        description=(
            "Offline converter: contemplative-agent audit JSONL logs "
            "(ADR-0075) -> OTLP traces with GenAI semantic conventions."
        ),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=_DEFAULT_LOG_DIR,
        help=f"audit log directory (default: {_DEFAULT_LOG_DIR})",
    )
    parser.add_argument(
        "--date",
        help="UTC day to convert, YYYY-MM-DD (default: all llm-calls-*.jsonl files)",
    )
    parser.add_argument(
        "--gap-seconds",
        type=float,
        default=300.0,
        help="silence gap that starts a new 'agent run' trace (default: 300)",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help=f"OTLP/HTTP endpoint (default: {_DEFAULT_ENDPOINT} unless --to-file is given)",
    )
    parser.add_argument(
        "--to-file",
        type=Path,
        default=None,
        help="write OTLP/JSON to this file instead of (or in addition to) the endpoint",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print a conversion summary without emitting anything",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)

    records = load_records(args.log_dir, date=args.date)
    if not records:
        print(f"no records found in {args.log_dir}" + (f" for {args.date}" if args.date else ""))
        return 1
    runs = group_into_runs(records, gap_s=args.gap_seconds)
    summary = summarize(runs)

    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    exporters = []
    if args.to_file is not None:
        exporters.append(OtlpJsonFileExporter(args.to_file))
    if args.endpoint is not None or args.to_file is None:
        exporters.append(make_otlp_http_exporter(args.endpoint or _DEFAULT_ENDPOINT))

    total = 0
    for exporter in exporters:
        provider = build_provider(exporter, expected_spans=int(summary["spans"]))
        total = emit_runs(runs, provider.get_tracer("contemplative-agent-otel"), args.gap_seconds)
        provider.force_flush()
        provider.shutdown()

    destinations = " + ".join(
        ([str(args.to_file)] if args.to_file else [])
        + (
            [args.endpoint or _DEFAULT_ENDPOINT]
            if (args.endpoint is not None or args.to_file is None)
            else []
        )
    )
    print(f"emitted {total} spans across {summary['runs']} runs -> {destinations}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
