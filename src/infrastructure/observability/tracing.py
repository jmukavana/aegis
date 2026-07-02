import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from src.infrastructure.observability.resource import get_resource


def configure_tracing() -> None:
    provider = TracerProvider(resource=get_resource())

    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        f"{os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:5318')}/v1/traces",
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
