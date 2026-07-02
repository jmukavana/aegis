from fastapi import APIRouter, Depends
from opentelemetry import trace
from pydantic import BaseModel

from src.application.correlation.use_cases import CorrelateSignal
from src.domain.change.change_log import ChangeLog
from src.domain.incident.entities import Signal
from src.domain.incident.value_objects import Fingerprint, Severity
from src.domain.topology.service_graph import ServiceGraph
from src.infrastructure.persistence.incident_repository import (
    SqlAlchemyIncidentRepository,
)

tracer = trace.get_tracer(__name__)


class SignalRequest(BaseModel):
    fingerprint: str  # "service/failure_class/resource"
    source: str
    severity: Severity
    summary: str


def build_signal_router(
    signals_ingested,
    logger,
    get_session,
    service_graph: ServiceGraph,
    change_log: ChangeLog,
) -> APIRouter:
    router = APIRouter(prefix="/signals", tags=["signals"])

    @router.post("", status_code=202)
    async def ingest_signal(body: SignalRequest, session=Depends(get_session)):
        signal = Signal.observe(
            fingerprint=Fingerprint.parse(body.fingerprint),
            source=body.source,
            severity=body.severity,
            summary=body.summary,
        )

        with tracer.start_as_current_span("correlate_signal") as span:
            result = await CorrelateSignal(
                SqlAlchemyIncidentRepository(session), service_graph, change_log
            ).execute(signal)
            span.set_attribute("incident.id", str(result.incident.id))
            span.set_attribute("incident.newly_opened", result.newly_opened)
            logger.info(
                "Signal %s -> incident %s (%s)",
                signal.fingerprint,
                result.incident.id,
                "opened" if result.newly_opened else "correlated",
            )

        signals_ingested.add(
            1, {"outcome": "opened" if result.newly_opened else "correlated"}
        )
        return {
            "incident_id": str(result.incident.id),
            "newly_opened": result.newly_opened,
            "status": result.incident.status.value,
            "suspected_change": result.incident.suspected_change,
        }

    return router
