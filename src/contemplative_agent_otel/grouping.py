"""Cluster merged audit records into "agent run" groups.

Preferred grouping is the ``run_id`` the logs carry (one per process
execution, stamped by the main repository's shared audit writer since
ADR-0078's follow-up) — that is measurement, not inference.

Records without a ``run_id`` (logs written before the field existed) fall
back to time-gap clustering: records sorted by start time are split whenever
the gap between a record's start and the latest end seen so far exceeds the
threshold. That matches the agent's launchd cadence (bursts of activity
separated by silence) but it is a reconstruction — each Run says which kind
it is via ``grouping``, and the emitter surfaces it as ``ca.convert.grouping``.
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
    grouping: str = "time-gap"  # "run-id" | "time-gap"


def group_into_runs(records: Sequence[Record], gap_s: float = 300.0) -> list[Run]:
    """Group records into runs: by ``run_id`` where present, else by time gap.

    A mixed corpus (old records without run_id, new records with it) yields
    both kinds side by side, sorted by start time.
    """
    # Truthy check (not `is not None`): an empty-string id would merge
    # unrelated records into one synthetic run — treat it as absent.
    with_id = [r for r in records if r.run_id]
    without_id = [r for r in records if not r.run_id]

    runs: list[Run] = []

    by_id: dict[str, list[Record]] = {}
    for record in sorted(with_id, key=lambda r: r.start_ns):
        by_id.setdefault(record.run_id, []).append(record)
    for group in by_id.values():
        runs.append(_finish(group, grouping="run-id"))

    runs.extend(_group_by_time_gap(without_id, gap_s))
    runs.sort(key=lambda r: r.start_ns)
    return runs


def _group_by_time_gap(records: Sequence[Record], gap_s: float) -> list[Run]:
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
            runs.append(_finish(current, grouping="time-gap"))
            current = []
        current.append(record)
        current_end = max(current_end, record.end_ns) if len(current) > 1 else record.end_ns

    if current:
        runs.append(_finish(current, grouping="time-gap"))
    return runs


def _finish(records: list[Record], grouping: str) -> Run:
    return Run(
        start_ns=min(r.start_ns for r in records),
        end_ns=max(r.end_ns for r in records),
        records=tuple(records),
        grouping=grouping,
    )
