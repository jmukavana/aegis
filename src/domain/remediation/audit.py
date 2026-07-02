from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.value_objects import RemediationPlanId


@dataclass(frozen=True)
class AuditEntry:
    """One immutable line in the remediation audit trail.

    Every decision and action on a plan appends one of these — {detected, reasoned, decided,
    dry-run, approved-by, acted, outcome} (ARCHITECTURE.md §3.7). Non-negotiable for trust and
    postmortems, which is why the port below has no update or delete.
    """

    at: datetime
    incident_id: IncidentId
    plan_id: RemediationPlanId
    event: str
    detail: str


class AuditLog(ABC):
    """Append-only record of everything the remediation pipeline decides and does.

    Deliberately write-once: `append` + reads only, no mutation in the contract, so an adapter
    cannot quietly rewrite history.
    """

    @abstractmethod
    async def append(self, entry: AuditEntry) -> None: ...

    @abstractmethod
    async def for_incident(self, incident_id: IncidentId) -> list[AuditEntry]: ...
