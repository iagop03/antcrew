"""OpenTelemetry bootstrap for antcrew-platform.

No-ops silently when the opentelemetry packages are not installed.
Activates when OTLP_ENDPOINT is set (defaults to http://localhost:4317).

Install (optional):
    pip install opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-grpc \
                opentelemetry-instrumentation-fastapi \
                opentelemetry-instrumentation-httpx \
                opentelemetry-instrumentation-sqlalchemy

Then run with:
    OTLP_ENDPOINT=http://otel-collector:4317 uvicorn app.main:app
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_OTLP_ENDPOINT = os.environ.get("OTLP_ENDPOINT", "")
_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "antcrew-platform")


def setup_tracing(app=None) -> None:
    """Configure OpenTelemetry tracing. Call once at app startup.

    Args:
        app: FastAPI app instance — passed to FastAPI instrumentor when present.
    """
    if not _OTLP_ENDPOINT:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.debug("opentelemetry-sdk not installed — tracing disabled")
        return

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        log.debug("opentelemetry-exporter-otlp-proto-grpc not installed — tracing disabled")
        return

    resource = Resource(attributes={SERVICE_NAME: _SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=_OTLP_ENDPOINT, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # FastAPI auto-instrumentation
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app)
            log.info("otel: FastAPI instrumented")
        except ImportError:
            pass

    # httpx auto-instrumentation (outbound LLM calls via keybridge)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        log.info("otel: httpx instrumented")
    except ImportError:
        pass

    # SQLAlchemy auto-instrumentation (async session)
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument()
        log.info("otel: SQLAlchemy instrumented")
    except ImportError:
        pass

    log.info("otel: tracing enabled → %s (service=%s)", _OTLP_ENDPOINT, _SERVICE_NAME)
