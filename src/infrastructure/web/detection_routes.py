from fastapi import APIRouter, Depends
from opentelemetry import trace

from src.application.correlation.use_cases import CorrelateSignal
from src.application.detection.detectors import Detector
from src.application.detection.run_cycle import RunDetectionCycle
from src.domain.change.change_log import ChangeLog
from src.domain.topology.service_graph import ServiceGraph
from src.infrastructure.persistence.incident_repository import (
    SqlAlchemyIncidentRepository,
)

tracer = trace.get_tracer(__name__)


def build_detection_router(
    logger,
    get_session,
    detectors: list[Detector],
    service_graph: ServiceGraph,
    change_log: ChangeLog,
) -> APIRouter:
    router = APIRouter(prefix="/detect", tags=["detection"])

    @router.post("", status_code=202)
    async def run_detection_cycle(session=Depends(get_session)):
        """Run one detection cycle on demand (needs a reachable Prometheus)."""
        correlate = CorrelateSignal(
            SqlAlchemyIncidentRepository(session), service_graph, change_log
        )
        with tracer.start_as_current_span("detection_cycle") as span:
            results = await RunDetectionCycle(detectors, correlate).execute()
            opened = sum(1 for r in results if r.newly_opened)
            span.set_attribute("detection.signals", len(results))
            span.set_attribute("detection.incidents_opened", opened)
            logger.info(
                "Detection cycle: %d signals, %d new incidents", len(results), opened
            )

        return {
            "signals": len(results),
            "incidents_opened": opened,
            "incident_ids": [str(r.incident.id) for r in results],
        }

    return router
