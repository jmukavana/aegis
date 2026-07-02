import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.domain.incident.entities import TERMINAL_STATUSES, Incident, Signal
from src.domain.incident.repository import IncidentRepository
from src.domain.incident.value_objects import (
    Fingerprint,
    IncidentId,
    IncidentStatus,
    Severity,
    SignalId,
)


def _serialize_signal(signal: Signal) -> dict:
    return {
        "id": str(signal.id),
        "fingerprint": str(signal.fingerprint),
        "source": signal.source,
        "severity": signal.severity.value,
        "summary": signal.summary,
        "observed_at": signal.observed_at.isoformat(),
    }


def _deserialize_signal(raw: dict) -> Signal:
    return Signal(
        id=SignalId(UUID(raw["id"])),
        fingerprint=Fingerprint.parse(raw["fingerprint"]),
        source=raw["source"],
        severity=Severity(raw["severity"]),
        summary=raw["summary"],
        observed_at=datetime.fromisoformat(raw["observed_at"]),
    )


class IncidentModel(SQLModel, table=True):
    __tablename__ = "incidents"

    id: UUID = Field(primary_key=True)
    fingerprint: str = Field(index=True)
    title: str
    status: str
    severity: str
    opened_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    last_activity_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    suspected_change: str | None = None
    # Phase 0 stores signals as a JSON blob on the incident. When correlation/query needs
    # grow (Phase 1+), promote this to a proper `signals` table with a FK to incidents.
    signals_json: str

    def to_entity(self) -> Incident:
        signals = tuple(_deserialize_signal(raw) for raw in json.loads(self.signals_json))
        return Incident(
            id=IncidentId(self.id),
            fingerprint=Fingerprint.parse(self.fingerprint),
            title=self.title,
            status=IncidentStatus(self.status),
            severity=Severity(self.severity),
            opened_at=self.opened_at,
            last_activity_at=self.last_activity_at,
            suspected_change=self.suspected_change,
            signals=signals,
        )

    @classmethod
    def from_entity(cls, incident: Incident) -> "IncidentModel":
        return cls(
            id=incident.id,
            fingerprint=str(incident.fingerprint),
            title=incident.title,
            status=incident.status.value,
            severity=incident.severity.value,
            opened_at=incident.opened_at,
            last_activity_at=incident.last_activity_at,
            suspected_change=incident.suspected_change,
            signals_json=json.dumps(
                [_serialize_signal(signal) for signal in incident.signals]
            ),
        )


class SqlAlchemyIncidentRepository(IncidentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, incident: Incident) -> None:
        self._session.add(IncidentModel.from_entity(incident))
        await self._session.commit()

    async def get_by_id(self, incident_id: IncidentId) -> Incident | None:
        model = await self._session.get(IncidentModel, incident_id)
        return model.to_entity() if model else None

    async def update(self, incident: Incident) -> None:
        await self._session.merge(IncidentModel.from_entity(incident))
        await self._session.commit()

    async def list_all(self) -> list[Incident]:
        result = await self._session.exec(select(IncidentModel))
        return [model.to_entity() for model in result.all()]

    async def list_open(self) -> list[Incident]:
        terminal = [status.value for status in TERMINAL_STATUSES]
        statement = select(IncidentModel).where(
            col(IncidentModel.status).notin_(terminal)
        )
        result = await self._session.exec(statement)
        return [model.to_entity() for model in result.all()]
