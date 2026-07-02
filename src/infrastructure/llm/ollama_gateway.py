import httpx

from src.domain.analysis.gateway import IncidentAnalysisGateway
from src.domain.analysis.memory import RecalledIncident
from src.domain.analysis.models import IncidentAnalysis
from src.domain.incident.entities import Incident
from src.domain.runbook.spec import RunbookSpec
from src.infrastructure.llm.prompting import (
    SYSTEM_PROMPT,
    analysis_schema,
    incident_prompt,
    parse_analysis,
)


class OllamaIncidentAnalysisGateway(IncidentAnalysisGateway):
    """Local-LLM adapter (Ollama) for the incident-analysis port.

    Uses Ollama's structured-output `format` to constrain the reply to the shared analysis
    schema whose `runbook_id` is an enum of the catalog ids — so the model can only reference
    pre-vetted runbooks (the LLM boundary from ARCHITECTURE.md §3.4). Provider-neutral over
    httpx; swap this adapter to hit any other backend without touching the application layer.
    """

    def __init__(
        self, base_url: str, model: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=120.0)

    async def explain(
        self,
        incident: Incident,
        catalog: list[RunbookSpec],
        similar: list[RecalledIncident],
    ) -> IncidentAnalysis:
        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "stream": False,
                "format": analysis_schema(catalog),
                "options": {"temperature": 0.2},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": incident_prompt(incident, catalog, similar),
                    },
                ],
            },
        )
        response.raise_for_status()
        return parse_analysis(response.json()["message"]["content"])
