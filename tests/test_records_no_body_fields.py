"""Exporter records carry no body or b64 fields (P1-11.8).

Whatever reaches an exporter (including an off-host collector chosen with
``--endpoint``) is built from these dataclasses, so pinning their field names
pins what can leave. A field that holds text, a body or base64 would have to
be added here on purpose; a name that merely mentions such a word must be a
derived measure (hash, length, count, categorical status).
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from contemplative_agent_otel.records import ApiCall, LlmCall, VerificationEvent

_BODYISH = re.compile(r"prompt|system|completion|response|body|text|b64|challenge|content|error")
_DERIVED_SUFFIX = re.compile(r"(_sha256|_chars|_bytes|_count|_status|_kind|_encoding|_truncated)$")
_FORBIDDEN_EXACT = {
    "prompt",
    "system",
    "completion",
    "response",
    "response_text",
    "body",
    "text",
    "challenge",
    "challenge_b64",
    "error",
    "error_body",
    "content",
}
# Derived or categorical names that mention a bodyish word legitimately.
_ALLOWED = {
    "transport_error",  # categorical class
    "error_kind",
    "error_status",
}


@pytest.mark.parametrize("record", [LlmCall, VerificationEvent, ApiCall])
def test_record_fields_have_no_body_or_b64(record: type) -> None:
    names = [f.name for f in dataclasses.fields(record)]
    assert not (set(names) & _FORBIDDEN_EXACT)
    for name in names:
        if _BODYISH.search(name):
            assert name in _ALLOWED or _DERIVED_SUFFIX.search(name), (
                f"{record.__name__}.{name} looks like a body field; "
                "if it is a derived measure, give it a derived suffix"
            )
