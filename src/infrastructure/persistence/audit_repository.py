from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.audit import AuditEntry, AuditLog
from src.domain.remediation.value_objects import RemediationPlanId


class AuditEntryModel(SQLModel, table=True):
    __tablename__ = "audit_entries"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    incident_id: UUID = Field(index=True)
    plan_id: UUID = Field(index=True)
    event: str
    detail: str

    def to_entity(self) -> AuditEntry:
        return AuditEntry(
            at=self.at,
            incident_id=IncidentId(self.incident_id),
            plan_id=RemediationPlanId(self.plan_id),
            event=self.event,
            detail=self.detail,
        )


class SqlAlchemyAuditLog(AuditLog):
    """Append-only Postgres audit trail. Exposes only append + read — no update/delete — so the
    record cannot be rewritten (ARCHITECTURE.md §3.7 immutable audit)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, entry: AuditEntry) -> None:
        self._session.add(
            AuditEntryModel(
                at=entry.at,
                incident_id=entry.incident_id,
                plan_id=entry.plan_id,
                event=entry.event,
                detail=entry.detail,
            )
        )
        await self._session.commit()

    async def for_incident(self, incident_id: IncidentId) -> list[AuditEntry]:
        statement = (
            select(AuditEntryModel)
            .where(col(AuditEntryModel.incident_id) == incident_id)
            .order_by(col(AuditEntryModel.at))
        )
        result = await self._session.exec(statement)
        return [model.to_entity() for model in result.all()]
