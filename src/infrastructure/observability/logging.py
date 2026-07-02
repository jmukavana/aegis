import logging

from opentelemetry.instrumentation.logging import LoggingInstrumentor


def configure_logging() -> logging.Logger:
    # Injects trace_id/span_id + service.name into every log record so Loki<->Tempo
    # correlation works. Call AFTER configure_tracing() so the tracer provider (with its
    # resource) already exists when the instrumentor captures it.
    LoggingInstrumentor().instrument(set_logging_format=True)
    return logging.getLogger(__name__)
