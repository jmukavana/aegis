import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from src.domain.analysis.memory import RecalledIncident
from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import Fingerprint, Severity, SignalId
from src.infrastructure.llm.ollama_gateway import OllamaIncidentAnalysisGateway
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
async def test_gateway_constrains_runbook_ids_and_parses_reply():
    catalog = StaticRunbookCatalog().list_specs()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
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
            200, json={"message": {"role": "assistant", "content": json.dumps(reply)}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OllamaIncidentAnalysisGateway("http://ollama:11434", "llama3.1", client)

    similar = [
        RecalledIncident(
            title="checkout: prior oom",
            fingerprint="checkout/oom/prod",
            root_cause="leak in cart serializer",
            similarity=0.9,
        )
    ]
    analysis = await gateway.explain(_incident(), catalog, similar)

    # The request constrains runbook_id to the catalog enum (the LLM can't invent ids).
    schema = captured["body"]["format"]
    enum = schema["properties"]["proposals"]["items"]["properties"]["runbook_id"]["enum"]
    assert "rolling-restart" in enum and "delete-prod-database" not in enum
    # Recalled past incidents are injected into the prompt as RAG context.
    user_msg = captured["body"]["messages"][-1]["content"]
    assert "leak in cart serializer" in user_msg
    # The structured reply parses into the domain model.
    assert analysis.root_cause == "memory leak"
    assert analysis.proposals[0].confidence == pytest.approx(0.82)
    assert str(analysis.proposals[0].runbook_id) == "rolling-restart"
