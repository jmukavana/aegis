from abc import ABC, abstractmethod

from src.domain.analysis.models import IncidentAnalysis
from src.domain.incident.value_objects import IncidentId


class IncidentNotifier(ABC):
    """Port over the ChatOps channel that surfaces an incident analysis to humans.

    Read-only by construction (Phase 2): it *tells* on-call what was diagnosed and which
    pre-vetted runbooks were proposed — it never offers a control to execute one. Interactive
    approve/execute affordances are a Phase 3 concern, deliberately not modelled here. Kept
    behind a port so the channel (Slack today) is swappable and the delivery is best-effort:
    a notifier failure must never fail the explanation it is reporting on.
    """

    @abstractmethod
    async def notify_analysis(
        self, incident_id: IncidentId, analysis: IncidentAnalysis
    ) -> None: ...
