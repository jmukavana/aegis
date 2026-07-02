import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from src.application.incident.lifecycle import ResolveStaleIncidents
from src.infrastructure.persistence.incident_repository import (
    SqlAlchemyIncidentRepository,
)


async def run_reconcile_loop(
    session_factory: async_sessionmaker[AsyncSession],
    logger,
    interval_seconds: float,
) -> None:
    """Periodically auto-resolve stale incidents (read-only; only mutates incident state).

    Resilient by design: a transient DB error logs and the loop continues rather than
    killing the reconcile daemon. Cancelled on app shutdown via the lifespan handler.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with session_factory() as session:
                resolved = await ResolveStaleIncidents(
                    SqlAlchemyIncidentRepository(session)
                ).execute(now=datetime.now(timezone.utc))
            if resolved:
                logger.info("Auto-resolved %d stale incident(s)", len(resolved))
        except Exception:  # noqa: BLE001 - a daemon loop must survive transient failures
            logger.exception("Reconcile loop iteration failed; continuing")
