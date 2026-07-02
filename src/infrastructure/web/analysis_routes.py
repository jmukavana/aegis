from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from opentelemetry import trace

from src.application.analysis.use_cases import ExplainIncident
from src.domain.analysis.embedding import EmbeddingGateway
from src.domain.analysis.gateway import IncidentAnalysisGateway
from src.domain.analysis.models import IncidentAnalysis
from src.domain.incident.exceptions import IncidentNotFoundError
from src.domain.incident.value_objects import IncidentId
from src.domain.notification.notifier import IncidentNotifier
from src.domain.runbook.spec import RunbookCatalog
from src.infrastructure.persistence.incident_memory import PgVectorIncidentMemory
from src.infrastructure.persistence.incident_repository import (
    SqlAlchemyIncidentRepository,
)

tracer = trace.get_tracer(__name__)


def _serialize(analysis: IncidentAnalysis) -> dict:
    return {
        "summary": analysis.summary,
        "root_cause": analysis.root_cause,
        "proposals": [
            {
                "runbook_id": str(p.runbook_id),
                "parameters": p.parameters,
                "reasoning": p.reasoning,
                "confidence": p.confidence,
            }
            for p in analysis.proposals
        ],
    }


def build_analysis_router(
    logger,
    get_session,
    gateway: IncidentAnalysisGateway | None,
    catalog: RunbookCatalog,
    embedder: EmbeddingGateway | None = None,
    recall_limit: int = 3,
    notifier: IncidentNotifier | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/incidents", tags=["analysis"])

    @router.post("/{incident_id}/explain")
    async def explain_incident(incident_id: UUID, session=Depends(get_session)):
        if gateway is None:
            raise HTTPException(
                status_code=503,
                detail="LLM analysis disabled (no ollama_url configured).",
            )

        # Memory is optional: without an embedder the explainer runs without RAG context.
        memory = (
            PgVectorIncidentMemory(session, embedder) if embedder is not None else None
        )

        with tracer.start_as_current_span("explain_incident") as span:
            span.set_attribute("incident.id", str(incident_id))
            span.set_attribute("analysis.rag", memory is not None)
            try:
                analysis = await ExplainIncident(
                    gateway,
                    SqlAlchemyIncidentRepository(session),
                    catalog,
                    memory,
                    recall_limit,
                ).execute(IncidentId(incident_id))
            except IncidentNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error

            span.set_attribute("analysis.proposals", len(analysis.proposals))
            logger.info(
                "Explained incident %s: %d proposal(s)",
                incident_id,
                len(analysis.proposals),
            )

            # Best-effort ChatOps surfacing: a Slack failure must not fail the explanation.
            if notifier is not None:
                try:
                    await notifier.notify_analysis(IncidentId(incident_id), analysis)
                    span.set_attribute("analysis.notified", True)
                except Exception:  # noqa: BLE001 - surfacing is advisory, never fatal
                    span.set_attribute("analysis.notified", False)
                    logger.warning(
                        "Failed to surface incident %s to ChatOps", incident_id
                    )

        return _serialize(analysis)

    return router
