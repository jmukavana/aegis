from dataclasses import replace

from src.domain.analysis.gateway import IncidentAnalysisGateway
from src.domain.analysis.memory import IncidentMemory
from src.domain.analysis.models import IncidentAnalysis
from src.domain.incident.exceptions import IncidentNotFoundError
from src.domain.incident.repository import IncidentRepository
from src.domain.incident.value_objects import IncidentId
from src.domain.runbook.spec import RunbookCatalog


class ExplainIncident:
    """Read-only Phase 2 planner: ask the LLM to explain an incident and propose fixes.

    Nothing here executes anything. As a defense-in-depth backstop to the schema-level
    constraint in the adapter, any proposal referencing a runbook id not in the catalog is
    dropped before the analysis is returned — the planner can only surface pre-vetted actions.

    When an `IncidentMemory` is wired in, the planner is retrieval-augmented: it recalls
    similar past incidents to prime the model, then remembers this incident and its analysis
    so future explanations can draw on it. Memory is optional — without it the planner still
    works, just without cross-incident context.
    """

    def __init__(
        self,
        gateway: IncidentAnalysisGateway,
        incidents: IncidentRepository,
        catalog: RunbookCatalog,
        memory: IncidentMemory | None = None,
        recall_limit: int = 3,
    ) -> None:
        self._gateway = gateway
        self._incidents = incidents
        self._catalog = catalog
        self._memory = memory
        self._recall_limit = recall_limit

    async def execute(self, incident_id: IncidentId) -> IncidentAnalysis:
        incident = await self._incidents.get_by_id(incident_id)
        if incident is None:
            raise IncidentNotFoundError(str(incident_id))

        similar = (
            await self._memory.recall(incident, self._recall_limit)
            if self._memory is not None
            else []
        )
        specs = self._catalog.list_specs()
        analysis = self._drop_unknown_runbooks(
            await self._gateway.explain(incident, specs, similar)
        )

        if self._memory is not None:
            await self._memory.remember(incident, analysis)
        return analysis

    def _drop_unknown_runbooks(self, analysis: IncidentAnalysis) -> IncidentAnalysis:
        known = tuple(
            proposal
            for proposal in analysis.proposals
            if self._catalog.get_spec(proposal.runbook_id) is not None
        )
        return replace(analysis, proposals=known)
