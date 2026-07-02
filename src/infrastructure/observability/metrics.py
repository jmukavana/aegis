import os
from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Counter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from src.infrastructure.observability.resource import get_resource


@dataclass(frozen=True)
class Metrics:
    incidents_recorded: Counter
    signals_ingested: Counter


def configure_metrics() -> Metrics:
    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        f"{os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:5318')}/v1/metrics",
    )
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
    metrics.set_meter_provider(
        MeterProvider(resource=get_resource(), metric_readers=[reader])
    )
    meter = metrics.get_meter(__name__)
    return Metrics(
        incidents_recorded=meter.create_counter(
            "aegis_incidents_recorded",
            description="Count of incidents recorded by hand",
        ),
        signals_ingested=meter.create_counter(
            "aegis_signals_ingested",
            description="Count of signals ingested through correlation",
        ),
    )
