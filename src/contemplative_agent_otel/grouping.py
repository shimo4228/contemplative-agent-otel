"""Cluster merged audit records into "agent run" groups by time gap.

The audit logs carry no session/run identifier, so trace grouping is a
reconstruction: records sorted by start time are split whenever the gap
between a record's start and the latest end seen so far exceeds the
threshold. This matches the agent's actual launchd cadence (bursts of
activity separated by silence) but it is inference, not ground truth —
the emitter marks it as such via ``ca.convert.*`` attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .records import Record


@dataclass(frozen=True)
class Run:
    start_ns: int
    end_ns: int
    records: tuple[Record, ...]


def group_into_runs(records: Sequence[Record], gap_s: float = 300.0) -> list[Run]:
    """Split time-sorted records into runs at gaps larger than ``gap_s``.

    The gap is measured from the latest ``end_ns`` seen in the current run
    (an in-flight 80s LLM call keeps the run alive) to the next record's
    ``start_ns``. A gap of exactly ``gap_s`` stays in the same run.
    """
    ordered = sorted(records, key=lambda r: r.start_ns)
    gap_ns = int(gap_s * 1_000_000_000)

    runs: list[Run] = []
    current: list[Record] = []
    current_end = 0

    for record in ordered:
        if current and record.start_ns - current_end > gap_ns:
            runs.append(_finish(current))
            current = []
        current.append(record)
        current_end = max(current_end, record.end_ns) if len(current) > 1 else record.end_ns

    if current:
        runs.append(_finish(current))
    return runs


def _finish(records: list[Record]) -> Run:
    return Run(
        start_ns=min(r.start_ns for r in records),
        end_ns=max(r.end_ns for r in records),
        records=tuple(records),
    )
