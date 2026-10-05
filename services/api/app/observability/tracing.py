"""Lazy OpenTelemetry assembly + domain spans (no-op without the lane).

The base production requirements deliberately exclude the OTel packages
(the optional requirements-observability.txt lane). Every hook here:

- is inert unless ``OTEL_TRACES_ENABLED=1``;
- degrades to a no-op (with one warning) when the packages are missing or
  the exporter is unreachable — telemetry must never fail requests;
- uses the domain span vocabulary (rag.retrieve, llm.call,
  speech.synthesize, classroom.stage, …) so spans stay comparable across
  services.
"""
from __future__ import annotations

import contextlib
import logging
import os
from typing import Iterator

log = logging.getLogger(__name__)

_STATE = {"initialized": False, "available": False}


def tracing_enabled() -> bool:
    return os.getenv("OTEL_TRACES_ENABLED", "0").strip() == "1"


def init_tracing(app) -> bool:
    """Wire FastAPI/HTTPX instrumentation lazily. Safe to call repeatedly."""
    if _STATE["initialized"]:
        return _STATE["available"]
    _STATE["initialized"] = True
    if not tracing_enabled():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter)
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:
        log.warning("OTEL_TRACES_ENABLED=1 but the observability lane is "
                    "not installed (%s): tracing stays a no-op. Install "
                    "requirements-observability.txt to enable it.",
                    type(exc).__name__)
        return False

    service = os.getenv("OTEL_SERVICE_NAME", "next-tutor-api")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    provider = TracerProvider(resource=Resource.create(
        {"service.name": service}))
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    try:
        from opentelemetry.instrumentation.fastapi import (
            FastAPIInstrumentor)
        from opentelemetry.instrumentation.httpx import (
            HTTPXClientInstrumentor)

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready")
        HTTPXClientInstrumentor().instrument()
    except Exception:
        log.warning("OTel instrumentation failed to attach; continuing "
                    "without request spans.", exc_info=True)
    _STATE["available"] = True
    log.info("OTel tracing enabled (service=%s endpoint=%s)",
             service, endpoint or "default")
    return True


def shutdown_tracing() -> None:
    """Best-effort flush on shutdown; no-op when never initialized."""
    if not _STATE["available"]:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=2000)  # type: ignore[attr-defined]
        if hasattr(provider, "shutdown"):
            provider.shutdown()  # type: ignore[attr-defined]
    except Exception:
        log.debug("OTel shutdown failed", exc_info=True)
    finally:
        _STATE["available"] = False
        _STATE["initialized"] = False


@contextlib.contextmanager
def domain_span(name: str,
                attributes: dict[str, object] | None = None) -> Iterator[None]:
    """Named domain span (rag.retrieve / llm.call / speech.synthesize …).

    Active only with tracing; otherwise a transparent context manager, so
    call sites never branch on telemetry availability.
    """
    if not _STATE["available"]:
        yield
        return
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("next-tutor.domain")
        with tracer.start_as_current_span(name) as span:
            for key, value in (attributes or {}).items():
                span.set_attribute(key, value)
            yield
    except Exception:
        # Never let telemetry break the domain call.
        yield


def reset_tracing_state() -> None:
    """Tests: forget initialization so env toggles re-evaluate."""
    _STATE["initialized"] = False
    _STATE["available"] = False
