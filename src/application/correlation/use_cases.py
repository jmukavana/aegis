from dataclasses import dataclass
from datetime import timedelta

from src.domain.change.change_log import ChangeLog
from src.domain.incident.entities import Incident, Signal
from src.domain.incident.repository import IncidentRepository
from src.domain.topology.service_graph import ServiceGraph

DEFAULT_CORRELATION_WINDOW = timedelta(minutes=10)


@dataclass(frozen=True)
class CorrelationResult:
    incident: Incident
    newly_opened: bool


class CorrelateSignal:
    """Turn an incoming signal into (or onto) an incident (ARCHITECTURE.md §3.3).

    Grouping is applied in order: fingerprint dedup -> topological grouping -> open a new
    incident (with change attribution). This is what collapses "20 alerts" into "1 root
    cause" and is the backbone of read-only Phase 1 detection.
    """

    def __init__(
        self,
        incidents: IncidentRepository,
        service_graph: ServiceGraph,
        change_log: ChangeLog,
        correlation_window: timedelta = DEFAULT_CORRELATION_WINDOW,
    ) -> None:
        self._incidents = incidents
        self._service_graph = service_graph
        self._change_log = change_log
        self._window = correlation_window

    async def execute(self, signal: Signal) -> CorrelationResult:
        open_incidents = await self._incidents.list_open()
        window_start = signal.observed_at - self._window
        active = [i for i in open_incidents if i.last_activity_at >= window_start]

        match = self._dedup_by_fingerprint(active, signal) or self._upstream_cause(
            active, signal
        )
        if match is not None:
            updated = match.attach(signal)
            await self._incidents.update(updated)
            return CorrelationResult(incident=updated, newly_opened=False)

        incident = Incident.open(
            title=self._title_for(signal),
            initial_signal=signal,
            suspected_change=await self._suspected_change(signal, window_start),
        )
        await self._incidents.add(incident)
        return CorrelationResult(incident=incident, newly_opened=True)

    @staticmethod
    def _dedup_by_fingerprint(
        active: list[Incident], signal: Signal
    ) -> Incident | None:
        return next(
            (i for i in active if i.fingerprint == signal.fingerprint), None
        )

    def _upstream_cause(
        self, active: list[Incident], signal: Signal
    ) -> Incident | None:
        """Attach to an open incident whose failing service is a dependency of this signal's
        service — i.e. this signal is downstream fallout of a known upstream failure."""
        dependencies = self._service_graph.dependencies_of(signal.fingerprint.service)
        return next(
            (i for i in active if i.fingerprint.service in dependencies), None
        )

    async def _suspected_change(self, signal: Signal, window_start) -> str | None:
        changes = await self._change_log.recent_changes(
            signal.fingerprint.service, window_start
        )
        return changes[0].description if changes else None

    @staticmethod
    def _title_for(signal: Signal) -> str:
        fp = signal.fingerprint
        return f"{fp.service}: {fp.failure_class}"
