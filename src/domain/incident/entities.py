from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.incident.value_objects import (
    Fingerprint,
    IncidentId,
    IncidentStatus,
    Severity,
    SignalId,
)


@dataclass(frozen=True)
class Signal:
    """One detector firing — cheap, noisy, high-volume.

    Many signals correlate into one Incident. In Phase 0 signals are recorded by hand or by
    the k8s event watcher; automated detectors arrive in Phase 1.
    """

    id: SignalId
    fingerprint: Fingerprint
    source: str
    severity: Severity
    summary: str
    observed_at: datetime

    @classmethod
    def observe(
        cls,
        fingerprint: Fingerprint,
        source: str,
        severity: Severity,
        summary: str,
    ) -> "Signal":
        return cls(
            id=SignalId(uuid4()),
            fingerprint=fingerprint,
            source=source,
            severity=severity,
            summary=summary,
            observed_at=datetime.now(timezone.utc),
        )


TERMINAL_STATUSES = frozenset({IncidentStatus.RESOLVED, IncidentStatus.ESCALATED})


@dataclass(frozen=True)
class Incident:
    """A correlated, human-meaningful unit of work (what gets reasoned about and acted on).

    The `signals -> incident` relationship is many-to-one; see ARCHITECTURE.md §3.3.
    """

    id: IncidentId
    fingerprint: Fingerprint
    title: str
    status: IncidentStatus
    severity: Severity
    opened_at: datetime
    last_activity_at: datetime
    suspected_change: str | None = None
    signals: tuple[Signal, ...] = field(default_factory=tuple)

    @classmethod
    def open(
        cls,
        title: str,
        initial_signal: Signal,
        suspected_change: str | None = None,
    ) -> "Incident":
        return cls(
            id=IncidentId(uuid4()),
            fingerprint=initial_signal.fingerprint,
            title=title,
            status=IncidentStatus.OPEN,
            severity=initial_signal.severity,
            opened_at=initial_signal.observed_at,
            last_activity_at=initial_signal.observed_at,
            suspected_change=suspected_change,
            signals=(initial_signal,),
        )

    @property
    def is_open(self) -> bool:
        return self.status not in TERMINAL_STATUSES

    def attach(self, signal: Signal) -> "Incident":
        return replace(
            self,
            signals=self.signals + (signal,),
            last_activity_at=max(self.last_activity_at, signal.observed_at),
        )

    def transition_to(self, status: IncidentStatus) -> "Incident":
        return replace(self, status=status)

    def resolve(self) -> "Incident":
        return self.transition_to(IncidentStatus.RESOLVED)
