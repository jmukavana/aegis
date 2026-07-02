"""Standalone Kubernetes event-watcher process.

Streams cluster events, turns the ones we track into signals, and runs them through the same
correlation engine + Postgres the API uses — so a crash-looping pod shows up as an incident.

    KUBE_CONTEXT=minikube uv run python -m src.watch_events

Read-only w.r.t. the cluster (Phase 1): it only *observes* events, never mutates anything.
"""

import asyncio

from src.application.correlation.use_cases import CorrelateSignal
from src.application.incident.use_cases import ObservedSignal
from src.domain.incident.entities import Signal
from src.infrastructure.change.in_memory_change_log import InMemoryChangeLog
from src.infrastructure.config import get_settings
from src.infrastructure.db import create_db_engine, create_session_factory
from src.infrastructure.kubernetes.event_watcher import run_event_watcher
from src.infrastructure.observability.logging import configure_logging
from src.infrastructure.observability.tracing import configure_tracing
from src.infrastructure.persistence.incident_repository import (
    SqlAlchemyIncidentRepository,
)
from src.infrastructure.topology.seed import SERVICE_DEPENDENCIES
from src.infrastructure.topology.static_service_graph import StaticServiceGraph


async def main() -> None:
    settings = get_settings()
    if not settings.kube_context:
        raise SystemExit("Set KUBE_CONTEXT (e.g. minikube) to run the event watcher.")

    configure_tracing()
    logger = configure_logging()

    session_factory = create_session_factory(create_db_engine(settings))
    service_graph = StaticServiceGraph(SERVICE_DEPENDENCIES)
    change_log = InMemoryChangeLog()

    async def correlate(observed: ObservedSignal) -> None:
        signal = Signal.observe(
            fingerprint=observed.fingerprint,
            source=observed.source,
            severity=observed.severity,
            summary=observed.summary,
        )
        async with session_factory() as session:
            result = await CorrelateSignal(
                SqlAlchemyIncidentRepository(session), service_graph, change_log
            ).execute(signal)
        logger.info(
            "k8s event %s -> incident %s (%s)",
            signal.fingerprint,
            result.incident.id,
            "opened" if result.newly_opened else "correlated",
        )

    logger.info("Watching Kubernetes events on context '%s'", settings.kube_context)
    await run_event_watcher(settings.kube_context, correlate)


if __name__ == "__main__":
    asyncio.run(main())
