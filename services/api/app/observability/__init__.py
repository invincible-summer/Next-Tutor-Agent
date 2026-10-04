"""Observability: request correlation, redaction, optional OTel tracing.

Three concerns, all opt-in-degrading:

- ``request_id``: X-Request-ID in/out propagation (client-supplied id is
  sanitized; the same id rides the response and every log line's context).
- ``redaction``: default-deny scrubbing for anything log-shaped — secrets,
  JWT/refresh material, prompts and message bodies never enter logs.
- ``tracing``: OpenTelemetry wired lazily behind OTEL_TRACES_ENABLED; when
  the optional observability dependency lane is not installed, every hook
  degrades to a no-op instead of failing requests.
"""
from __future__ import annotations
