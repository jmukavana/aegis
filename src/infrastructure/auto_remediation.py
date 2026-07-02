import asyncio
from collections.abc import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from src.application.remediation.use_cases import AutoRemediationSweep

BuildSweep = Callable[[AsyncSession], AutoRemediationSweep]


async def run_auto_remediation_loop(
    session_factory: async_sessionmaker[AsyncSession],
    logger,
    interval_seconds: float,
    build_sweep: BuildSweep,
) -> None:
    """Phase 4b closed-loop trigger: periodically sweep open incidents and drive AutoRemediate.

    This is the last human touchpoint removed — nobody calls an endpoint; the loop finds open
    incidents and lets the trust-ratchet policy decide (auto-heal the earned ones, leave the
    rest as pending plans for a human). Off by default; started from the lifespan only when
    `enable_auto_remediation_loop` is set. Resilient like the reconcile loop — a bad iteration
    logs and the daemon continues.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with session_factory() as session:
                acted = await build_sweep(session).run()
            if acted:
                logger.info("Auto-remediation swept %d open incident(s)", len(acted))
        except Exception:  # noqa: BLE001 - a daemon loop must survive transient failures
            logger.exception("Auto-remediation loop iteration failed; continuing")
