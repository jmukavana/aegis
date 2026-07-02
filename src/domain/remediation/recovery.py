from abc import ABC, abstractmethod

from src.domain.incident.entities import Incident


class RecoveryProbe(ABC):
    """Answers the Verify question (ARCHITECTURE.md §3.6): did the incident actually recover?

    After a remediation runs, this decides whether the triggering condition has cleared —
    which is what turns fire-and-forget into a closed loop, and what the flywheel records as the
    *real* outcome (an action earns trust by fixing incidents, not by an API call succeeding).
    Behind a port so the recovery signal — cluster/deployment health, an SLO burn rate going
    quiet, no new matching signals — is swappable per environment.
    """

    @abstractmethod
    async def is_recovered(self, incident: Incident) -> bool: ...
