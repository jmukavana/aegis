from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Integer, cast, func
from sqlmodel import Field, SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.domain.remediation.outcome import OutcomeStats, OutcomeStore
from src.domain.runbook.contract import RunbookId


class RemediationOutcomeModel(SQLModel, table=True):
    __tablename__ = "remediation_outcomes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    runbook_id: str = Field(index=True)
    fingerprint: str = Field(index=True)
    success: bool
    recorded_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))


class SqlAlchemyOutcomeStore(OutcomeStore):
    """Durable trust flywheel (Phase 4c): one row per verified outcome in Postgres.

    Success rates now survive restarts and are shared across replicas, so a runbook's *earned*
    autonomy is not lost when the process cycles (the in-memory store was a demo artifact).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self, runbook_id: RunbookId, fingerprint: str, success: bool
    ) -> None:
        self._session.add(
            RemediationOutcomeModel(
                runbook_id=str(runbook_id),
                fingerprint=fingerprint,
                success=success,
                recorded_at=datetime.now(timezone.utc),
            )
        )
        await self._session.commit()

    async def stats(self, runbook_id: RunbookId, fingerprint: str) -> OutcomeStats:
        successes = func.coalesce(func.sum(cast(col(RemediationOutcomeModel.success), Integer)), 0)
        statement = select(func.count(), successes).where(
            (col(RemediationOutcomeModel.runbook_id) == str(runbook_id))
            & (col(RemediationOutcomeModel.fingerprint) == fingerprint)
        )
        count, won = (await self._session.exec(statement)).one()
        if not count:
            return OutcomeStats(sample_size=0, success_rate=0.0)
        return OutcomeStats(sample_size=int(count), success_rate=int(won) / int(count))
