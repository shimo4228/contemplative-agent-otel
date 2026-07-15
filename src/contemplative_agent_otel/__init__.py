"""Offline JSONL-audit-log to OTLP trace converter for contemplative-agent.

Reads the main repository's replayable audit logs (ADR-0075) from
``MOLTBOOK_HOME/logs/`` and re-emits them as OpenTelemetry spans following
the GenAI semantic conventions. No runtime instrumentation: the agent
process is never touched, and this package has zero code dependency on
``contemplative-agent``.
"""

__version__ = "0.1.0"
