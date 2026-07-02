from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, func
from sqlmodel import Field, SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.circuit_breaker import CircuitBreaker


class CircuitBreakerActionModel(SQLModel, table=True):
    __tablename__ = "circuit_breaker_actions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    incident_id: UUID = Field(index=True)
    acted_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))


class SqlAlchemyCircuitBreaker(CircuitBreaker):
    """Durable, shared circuit breaker (Phase 4c): action counts live in Postgres.

    The cap now holds across restarts and replicas — a controller that cycles or scales out
    can't accidentally reset an incident's action count and start flapping.
    """

    def __init__(
        self, session: AsyncSession, max_actions: int, window: timedelta
    ) -> None:
        self._session = session
        self._max_actions = max_actions
        self._window = window

    async def allow(self, incident_id: IncidentId) -> bool:
        cutoff = datetime.now(timezone.utc) - self._window
        statement = select(func.count()).where(
            (col(CircuitBreakerActionModel.incident_id) == incident_id)
            & (col(CircuitBreakerActionModel.acted_at) >= cutoff)
        )
        recent = (await self._session.exec(statement)).one()
        return int(recent) < self._max_actions

    async def record(self, incident_id: IncidentId) -> None:
        self._session.add(
            CircuitBreakerActionModel(
                incident_id=incident_id, acted_at=datetime.now(timezone.utc)
            )
        )
        await self._session.commit()
