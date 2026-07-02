import os

from opentelemetry.sdk.resources import SERVICE_NAME, Resource


def get_resource() -> Resource:
    return Resource.create(
        {SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "observability-ai")}
    )
