"""Parse contemplative-agent audit JSONL logs into typed, frozen records.

Security boundary of the whole converter lives here: untrusted free-text
fields (``challenge_b64``, server response bodies in ``error``) are dropped
at parse time — the dataclasses have no field to carry them, so later layers
cannot leak what was never retained. Only hashes, flags, and categorical
classifications survive.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional, Union

logger = logging.getLogger(__name__)

_HTTP_ERROR_RE = re.compile(r"API error (\d{3})")

_NS = 1_000_000_000


def to_epoch_ns(dt: datetime) -> int:
    """Convert an aware datetime to integer epoch nanoseconds."""
    return int(dt.timestamp() * _NS)


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def _classify_error(error: Any, *, solver_path: Any = None, answer: Any = None) -> Optional[str]:
    """Reduce a free-text error field to a categorical class, dropping the body."""
    if error is None:
        return None
    text = str(error)
    match = _HTTP_ERROR_RE.search(text)
    if match:
        return f"http_{match.group(1)}"
    if solver_path == "none" or answer is None and solver_path is not None:
        return "abstain"
    return "error"


@dataclass(frozen=True)
class LlmCall:
    ts: datetime
    caller: Optional[str]
    model: Optional[str]
    duration_ms: Optional[float]
    outcome: Optional[str]
    done_reason: Optional[str]
    prompt_eval_count: Optional[int]
    eval_count: Optional[int]
    cached_tokens: Optional[int]
    num_predict: Optional[int]
    temperature: Optional[float]
    think: Optional[bool]
    has_format: Optional[bool]
    prompt_chars: Optional[int]
    system_chars: Optional[int]
    prompt_sha256: Optional[str]
    error_kind: Optional[str]
    run_id: Optional[str] = None
    session_id: Optional[str] = None

    @property
    def start_ns(self) -> int:
        return to_epoch_ns(self.ts)

    @property
    def end_ns(self) -> int:
        if self.duration_ms is None:
            return self.start_ns
        return self.start_ns + int(self.duration_ms * 1_000_000)


@dataclass(frozen=True)
class VerificationEvent:
    ts: datetime
    challenge_sha256: Optional[str]
    challenge_encoding: Optional[str]
    challenge_bytes: Optional[int]
    challenge_truncated: Optional[bool]
    verification_code_sha256: Optional[str]
    answer: Optional[str]
    solver_path: Optional[str]
    solve_success: Optional[bool]
    verify_success: Optional[bool]
    error_status: Optional[str]  # categorical only; raw error body is dropped
    run_id: Optional[str] = None
    session_id: Optional[str] = None

    @property
    def start_ns(self) -> int:
        return to_epoch_ns(self.ts)

    @property
    def end_ns(self) -> int:
        return self.start_ns  # no duration in the log — zero-width, not estimated


@dataclass(frozen=True)
class ApiCall:
    ts: datetime
    method: Optional[str]
    endpoint: Optional[str]
    status: Optional[int]
    rate_remaining: Optional[int]
    success: Optional[bool]
    soft_fail: Optional[bool]
    drift_missing: tuple[str, ...]
    transport_error: Optional[str]
    retried: Optional[bool]
    retry_attempt: Optional[int]
    retry_max: Optional[int]
    retry_after_s: Optional[float]
    content_status: dict[str, Union[bool, int, float]]
    run_id: Optional[str] = None
    session_id: Optional[str] = None

    @property
    def start_ns(self) -> int:
        return to_epoch_ns(self.ts)

    @property
    def end_ns(self) -> int:
        return self.start_ns  # no duration in the log — zero-width, not estimated


Record = Union[LlmCall, VerificationEvent, ApiCall]


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed objects from a JSONL file, skipping blank/broken lines."""
    skipped = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(obj, dict):
                yield obj
    if skipped:
        logger.warning("%s: skipped %d unparseable line(s)", path.name, skipped)


def _opt_int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _opt_float(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _opt_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _opt_id(value: Any) -> Optional[str]:
    """Like _opt_str but treats "" as absent — a malformed/empty execution id
    must fall back to time-gap grouping, never form a synthetic merged run."""
    return value if isinstance(value, str) and value else None


def _opt_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _llm_call(obj: dict[str, Any]) -> Optional[LlmCall]:
    ts = _parse_ts(obj.get("ts"))
    if ts is None:
        return None
    return LlmCall(
        ts=ts,
        caller=_opt_str(obj.get("caller")),
        model=_opt_str(obj.get("model")),
        duration_ms=_opt_float(obj.get("duration_ms")),
        outcome=_opt_str(obj.get("outcome")),
        done_reason=_opt_str(obj.get("done_reason")),
        prompt_eval_count=_opt_int(obj.get("prompt_eval_count")),
        eval_count=_opt_int(obj.get("eval_count")),
        cached_tokens=_opt_int(obj.get("cached_tokens")),
        num_predict=_opt_int(obj.get("num_predict")),
        temperature=_opt_float(obj.get("temperature")),
        think=_opt_bool(obj.get("think")),
        has_format=_opt_bool(obj.get("has_format")),
        prompt_chars=_opt_int(obj.get("prompt_chars")),
        system_chars=_opt_int(obj.get("system_chars")),
        prompt_sha256=_opt_str(obj.get("prompt_sha256")),
        error_kind=_opt_str(obj.get("error_kind")),
        run_id=_opt_id(obj.get("run_id")),
        session_id=_opt_id(obj.get("session_id")),
    )


def _verification_event(obj: dict[str, Any]) -> Optional[VerificationEvent]:
    ts = _parse_ts(obj.get("ts"))
    if ts is None:
        return None
    return VerificationEvent(
        ts=ts,
        challenge_sha256=_opt_str(obj.get("challenge_sha256")),
        challenge_encoding=_opt_str(obj.get("challenge_encoding")),
        challenge_bytes=_opt_int(obj.get("challenge_bytes")),
        challenge_truncated=_opt_bool(obj.get("challenge_truncated")),
        verification_code_sha256=_opt_str(obj.get("verification_code_sha256")),
        answer=_opt_str(obj.get("answer")),
        solver_path=_opt_str(obj.get("solver_path")),
        solve_success=_opt_bool(obj.get("solve_success")),
        verify_success=_opt_bool(obj.get("verify_success")),
        error_status=_classify_error(
            obj.get("error"),
            solver_path=obj.get("solver_path"),
            answer=obj.get("answer"),
        ),
        run_id=_opt_id(obj.get("run_id")),
        session_id=_opt_id(obj.get("session_id")),
    )


def _content_status(value: Any) -> dict[str, Union[bool, int, float]]:
    if not isinstance(value, dict):
        return {}
    # Keep machine-readable scalars only; server-provided strings are untrusted.
    return {
        k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, (bool, int, float))
    }


def _api_call(obj: dict[str, Any]) -> Optional[ApiCall]:
    ts = _parse_ts(obj.get("ts"))
    if ts is None:
        return None
    drift = obj.get("drift_missing")
    return ApiCall(
        ts=ts,
        method=_opt_str(obj.get("method")),
        endpoint=_opt_str(obj.get("endpoint")),
        status=_opt_int(obj.get("status")),
        rate_remaining=_opt_int(obj.get("rate_remaining")),
        success=_opt_bool(obj.get("success")),
        soft_fail=_opt_bool(obj.get("soft_fail")),
        drift_missing=tuple(x for x in drift if isinstance(x, str))
        if isinstance(drift, list)
        else (),
        transport_error=_opt_str(obj.get("transport_error")),
        retried=_opt_bool(obj.get("retried")),
        retry_attempt=_opt_int(obj.get("retry_attempt")),
        retry_max=_opt_int(obj.get("retry_max")),
        retry_after_s=_opt_float(obj.get("retry_after_s")),
        content_status=_content_status(obj.get("content_status")),
        run_id=_opt_id(obj.get("run_id")),
        session_id=_opt_id(obj.get("session_id")),
    )


def load_records(log_dir: Path, date: Optional[str] = None) -> list[Record]:
    """Load and merge all three audit logs, sorted by start time.

    ``date`` (``YYYY-MM-DD``) selects the matching ``llm-calls-{date}.jsonl``
    file and filters the single-file logs (verification, api) to that UTC day;
    when omitted, every ``llm-calls-*.jsonl`` file and all rows are loaded.
    """
    records: list[Record] = []

    if date is not None:
        llm_paths = [log_dir / f"llm-calls-{date}.jsonl"]
    else:
        llm_paths = sorted(log_dir.glob("llm-calls-*.jsonl"))
    for path in llm_paths:
        if not path.exists():
            logger.warning("%s: not found, skipping", path.name)
            continue
        for obj in iter_jsonl(path):
            record = _llm_call(obj)
            if record is not None:
                records.append(record)

    for name, factory in (
        ("verification-audit.jsonl", _verification_event),
        ("api-audit.jsonl", _api_call),
    ):
        path = log_dir / name
        if not path.exists():
            logger.warning("%s: not found, skipping", name)
            continue
        for obj in iter_jsonl(path):
            record = factory(obj)
            if record is None:
                continue
            if date is not None and not str(obj.get("ts", "")).startswith(date):
                continue
            records.append(record)

    records.sort(key=lambda r: r.start_ns)
    return records
