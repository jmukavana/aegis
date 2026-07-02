from abc import ABC, abstractmethod

from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.entities import RemediationPlan
from src.domain.remediation.value_objects import RemediationPlanId


class RemediationPlanRepository(ABC):
    """Port over remediation-plan storage. `save` is an upsert (plans advance through their
    lifecycle in place, keyed by id)."""

    @abstractmethod
    async def save(self, plan: RemediationPlan) -> None: ...

    @abstractmethod
    async def get(self, plan_id: RemediationPlanId) -> RemediationPlan | None: ...

    @abstractmethod
    async def exists_for_incident(self, incident_id: IncidentId) -> bool:
        """Whether any plan already exists for this incident — the autonomous loop's idempotency
        guard, so it plans an incident at most once."""
