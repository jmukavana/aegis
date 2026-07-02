from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.incident.lifecycle import ResolveStaleIncidents
from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import Fingerprint, Severity, SignalId
from tests.fakes import InMemoryIncidentRepository

BASE = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _incident_last_active_at(at: datetime) -> Incident:
    signal = Signal(
        id=SignalId(uuid4()),
        fingerprint=Fingerprint("checkout", "oom", "prod"),
        source="test",
        severity=Severity.CRITICAL,
        summary="oom",
        observed_at=at,
    )
    return Incident.open(title="checkout: oom", initial_signal=signal)


@pytest.mark.asyncio
async def test_resolves_incident_past_recovery_window():
    repo = InMemoryIncidentRepository()
    await repo.add(_incident_last_active_at(BASE))

    resolved = await ResolveStaleIncidents(
        repo, recovery_window=timedelta(minutes=15)
    ).execute(now=BASE + timedelta(minutes=20))

    assert len(resolved) == 1
    assert len(await repo.list_open()) == 0


@pytest.mark.asyncio
async def test_keeps_incident_still_within_recovery_window():
    repo = InMemoryIncidentRepository()
    await repo.add(_incident_last_active_at(BASE))

    resolved = await ResolveStaleIncidents(
        repo, recovery_window=timedelta(minutes=15)
    ).execute(now=BASE + timedelta(minutes=5))

    assert resolved == []
    assert len(await repo.list_open()) == 1
