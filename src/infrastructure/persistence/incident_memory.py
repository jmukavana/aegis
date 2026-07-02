from datetime import datetime, timezone
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, delete
from sqlmodel import Field, SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.domain.analysis.embedding import EmbeddingGateway
from src.domain.analysis.memory import IncidentMemory, RecalledIncident
from src.domain.analysis.models import IncidentAnalysis
from src.domain.incident.entities import Incident

# nomic-embed-text emits 768-d vectors; keep in lockstep with migration 0003 and the
# embedding_model setting. Changing the model means a new column dimension + re-embed.
EMBED_DIM = 768


class IncidentMemoryModel(SQLModel, table=True):
    __tablename__ = "incident_memory"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    incident_id: UUID = Field(index=True)
    fingerprint: str
    title: str
    summary: str
    root_cause: str
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    embedding: list[float] = Field(sa_column=Column(Vector(EMBED_DIM)))


def _incident_text(incident: Incident) -> str:
    """The text embedded for similarity — the incident's *symptoms*, not any diagnosis.

    Remember and recall embed the same shape so a fresh incident (which has no analysis yet)
    lands near past incidents with matching symptoms. The diagnosis is stored as payload, not
    mixed into the vector.
    """
    signals = " ".join(s.summary for s in incident.signals)
    return f"{incident.title}\n{incident.fingerprint}\n{signals}"


class PgVectorIncidentMemory(IncidentMemory):
    """pgvector-backed incident memory (Phase 2 RAG).

    Embeds incident symptoms via the injected `EmbeddingGateway` and stores/queries them in a
    Postgres `vector` column using cosine distance. Built per-request with the request's
    session, mirroring how repositories are constructed in the routers.
    """

    def __init__(self, session: AsyncSession, embedder: EmbeddingGateway) -> None:
        self._session = session
        self._embedder = embedder

    async def remember(self, incident: Incident, analysis: IncidentAnalysis) -> None:
        vector = list(await self._embedder.embed(_incident_text(incident)))
        # Re-explaining an incident replaces its memory row rather than accumulating dupes.
        await self._session.exec(
            delete(IncidentMemoryModel).where(
                col(IncidentMemoryModel.incident_id) == incident.id
            )
        )
        self._session.add(
            IncidentMemoryModel(
                incident_id=incident.id,
                fingerprint=str(incident.fingerprint),
                title=incident.title,
                summary=analysis.summary,
                root_cause=analysis.root_cause,
                created_at=datetime.now(timezone.utc),
                embedding=vector,
            )
        )
        await self._session.commit()

    async def recall(self, incident: Incident, limit: int) -> list[RecalledIncident]:
        vector = list(await self._embedder.embed(_incident_text(incident)))
        # cosine_distance is a pgvector comparator on the Vector column; untyped for mypy.
        distance = col(IncidentMemoryModel.embedding).cosine_distance(  # type: ignore[attr-defined]
            vector
        )
        statement = (
            select(IncidentMemoryModel, distance.label("distance"))
            # Never recall the incident being explained (it may already be remembered).
            .where(col(IncidentMemoryModel.incident_id) != incident.id)
            .order_by(distance)
            .limit(limit)
        )
        rows = (await self._session.exec(statement)).all()
        return [
            RecalledIncident(
                title=model.title,
                fingerprint=model.fingerprint,
                root_cause=model.root_cause,
                similarity=1.0 - float(dist),
            )
            for model, dist in rows
        ]
