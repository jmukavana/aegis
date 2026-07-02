from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from opentelemetry import trace
from pydantic import BaseModel

from src.application.incident.use_cases import (
    ListIncidents,
    ObservedSignal,
    RecordIncident,
    TransitionIncident,
)
from src.domain.incident.entities import Incident
from src.domain.incident.value_objects import (
    Fingerprint,
    IncidentId,
    IncidentStatus,
    Severity,
)
from src.infrastructure.persistence.incident_repository import (
    SqlAlchemyIncidentRepository,
)

tracer = trace.get_tracer(__name__)


class RecordIncidentRequest(BaseModel):
    title: str
    fingerprint: str  # "service/failure_class/resource"
    source: str
    severity: Severity
    summary: str


class TransitionRequest(BaseModel):
    status: IncidentStatus


def _serialize(incident: Incident) -> dict:
    return {
        "id": str(incident.id),
        "title": incident.title,
        "fingerprint": str(incident.fingerprint),
        "status": incident.status.value,
        "severity": incident.severity.value,
        "opened_at": incident.opened_at.isoformat(),
        "last_activity_at": incident.last_activity_at.isoformat(),
        "suspected_change": incident.suspected_change,
        "signal_count": len(incident.signals),
    }


def build_incident_router(incidents_recorded, logger, get_session) -> APIRouter:
    router = APIRouter(prefix="/incidents", tags=["incidents"])

    @router.post("", status_code=201)
    async def record_incident(
        body: RecordIncidentRequest, session=Depends(get_session)
    ):
        with tracer.start_as_current_span("record_incident") as span:
            signal = ObservedSignal(
                fingerprint=Fingerprint.parse(body.fingerprint),
                source=body.source,
                severity=body.severity,
                summary=body.summary,
            )
            incident = await RecordIncident(
                SqlAlchemyIncidentRepository(session)
            ).execute(body.title, signal)
            span.set_attribute("incident.id", str(incident.id))
            logger.info("Recorded incident %s (%s)", incident.id, incident.fingerprint)

        incidents_recorded.add(1, {"severity": body.severity.value})
        return _serialize(incident)

    @router.get("")
    async def list_incidents(session=Depends(get_session)):
        with tracer.start_as_current_span("list_incidents") as span:
            result = await ListIncidents(
                SqlAlchemyIncidentRepository(session)
            ).execute()
            span.set_attribute("incidents.count", len(result))
        return [_serialize(incident) for incident in result]

    @router.patch("/{incident_id}/status")
    async def transition_incident(
        incident_id: UUID, body: TransitionRequest, session=Depends(get_session)
    ):
        with tracer.start_as_current_span("transition_incident") as span:
            incident = await TransitionIncident(
                SqlAlchemyIncidentRepository(session)
            ).execute(IncidentId(incident_id), body.status)
            if incident is None:
                raise HTTPException(status_code=404, detail="Incident not found")
            span.set_attribute("incident.id", str(incident.id))
            logger.info("Incident %s -> %s", incident.id, body.status.value)
        return _serialize(incident)

    return router
