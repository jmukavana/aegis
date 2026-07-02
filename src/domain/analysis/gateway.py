from abc import ABC, abstractmethod

from src.domain.analysis.memory import RecalledIncident
from src.domain.analysis.models import IncidentAnalysis
from src.domain.incident.entities import Incident
from src.domain.runbook.spec import RunbookSpec


class IncidentAnalysisGateway(ABC):
    """Port over the LLM that explains an incident and proposes remediations.

    The adapter is given the incident context, the runbook catalog, and any similar past
    incidents recalled from memory (Phase 2 RAG), and must return a structured
    `IncidentAnalysis` whose proposals reference catalog ids — it never emits free-form
    commands. Kept behind this port so the whole planner is testable with a fake and the
    model/provider can change without touching the application layer.
    """

    @abstractmethod
    async def explain(
        self,
        incident: Incident,
        catalog: list[RunbookSpec],
        similar: list[RecalledIncident],
    ) -> IncidentAnalysis: ...
