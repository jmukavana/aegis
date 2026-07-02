from abc import ABC, abstractmethod

from src.domain.incident.entities import Incident
from src.domain.incident.value_objects import IncidentId


class IncidentRepository(ABC):
    """Port for persisting incidents. Adapters live in infrastructure/persistence."""

    @abstractmethod
    async def add(self, incident: Incident) -> None: ...

    @abstractmethod
    async def get_by_id(self, incident_id: IncidentId) -> Incident | None: ...

    @abstractmethod
    async def update(self, incident: Incident) -> None: ...

    @abstractmethod
    async def list_all(self) -> list[Incident]: ...

    @abstractmethod
    async def list_open(self) -> list[Incident]:
        """Incidents not in a terminal status — the candidates for correlation."""
