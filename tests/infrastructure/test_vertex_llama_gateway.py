import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import Fingerprint, Severity, SignalId
from src.infrastructure.llm.vertex_llama_gateway import (
    VertexLlamaIncidentAnalysisGateway,
)
from src.infrastructure.runbook.static_catalog import StaticRunbookCatalog


def _incident() -> Incident:
    signal = Signal(
        id=SignalId(uuid4()),
        fingerprint=Fingerprint("checkout", "oom", "prod"),
        source="test",
        severity=Severity.CRITICAL,
        summary="OOMKilled x5",
        observed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    return Incident.open(title="checkout: oom", initial_signal=signal)


@pytest.mark.asyncio
async def test_vertex_gateway_calls_openai_endpoint_with_enum_and_parses():
    catalog = StaticRunbookCatalog().list_specs()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        reply = {
            "summary": "checkout is OOMing",
            "root_cause": "memory leak",
            "proposals": [
                {
                    "runbook_id": "rolling-restart",
                    "parameters": "namespace=prod, deployment=checkout",
                    "reasoning": "reclaim memory now",
                    "confidence": 0.82,
                }
            ],
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(reply)}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = VertexLlamaIncidentAnalysisGateway(
        "aegis-501206",
        "us-central1",
        "meta/llama-3.3-70b-instruct-maas",
        client=client,
        token_provider=lambda: "fake-token",
    )

    analysis = await gateway.explain(_incident(), catalog, [])

    # Hits the region-scoped OpenAI-compatible endpoint with a bearer token.
    assert "us-central1-aiplatform.googleapis.com" in captured["url"]
    assert captured["url"].endswith("/endpoints/openapi/chat/completions")
    assert captured["auth"] == "Bearer fake-token"
    # runbook_id is constrained to the catalog enum via response_format json_schema.
    schema = captured["body"]["response_format"]["json_schema"]["schema"]
    enum = schema["properties"]["proposals"]["items"]["properties"]["runbook_id"]["enum"]
    assert "rolling-restart" in enum and "delete-prod-database" not in enum
    # The structured reply parses into the domain model.
    assert analysis.root_cause == "memory leak"
    assert analysis.proposals[0].confidence == pytest.approx(0.82)


def test_vertex_gateway_requires_project():
    with pytest.raises(ValueError):
        VertexLlamaIncidentAnalysisGateway("", "us-central1", "m", token_provider=lambda: "t")
