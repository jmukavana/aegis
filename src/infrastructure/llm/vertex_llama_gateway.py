from collections.abc import Callable

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

TokenProvider = Callable[[], str]


class VertexLlamaIncidentAnalysisGateway(IncidentAnalysisGateway):
    """Vertex AI (Llama Model-as-a-Service) adapter for the incident-analysis port.

    Talks to Vertex's OpenAI-compatible `chat/completions` endpoint, so the Llama model we ran
    locally under Ollama is now Google-managed with the *same* analysis contract: the shared
    `runbook_id` enum schema is passed as `response_format: json_schema`, and the use case still
    drops any unknown runbook as a backstop. Only the transport + auth differ from Ollama —
    another demonstration that the `IncidentAnalysisGateway` port makes the provider a swap.

    Auth is a bearer token from Application Default Credentials, resolved lazily (so building
    the gateway needs no network/creds and tests can inject a fake token provider).
    """

    def __init__(
        self,
        project: str,
        location: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        if not project:
            raise ValueError("Vertex project id is required (set VERTEX_PROJECT).")
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=120.0)
        self._token_provider = token_provider or _adc_token_provider()
        self._url = (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{location}/endpoints/openapi/chat/completions"
        )

    async def explain(
        self,
        incident: Incident,
        catalog: list[RunbookSpec],
        similar: list[RecalledIncident],
    ) -> IncidentAnalysis:
        response = await self._client.post(
            self._url,
            headers={"Authorization": f"Bearer {self._token_provider()}"},
            json={
                "model": self._model,
                "temperature": 0.2,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "incident_analysis",
                        "schema": analysis_schema(catalog),
                    },
                },
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
        return parse_analysis(response.json()["choices"][0]["message"]["content"])


def _adc_token_provider() -> TokenProvider:  # pragma: no cover - needs GCP credentials
    """A token provider backed by Application Default Credentials, refreshed on demand.

    Imported lazily so the module (and the app) load with no google-auth/creds available;
    only actually calling Vertex requires credentials.
    """
    credentials = None

    def provide() -> str:
        nonlocal credentials
        import google.auth
        import google.auth.transport.requests

        if credentials is None:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not credentials.valid:
            credentials.refresh(google.auth.transport.requests.Request())
        return credentials.token

    return provide
