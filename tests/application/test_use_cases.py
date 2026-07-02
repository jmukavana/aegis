import pytest

from src.application.incident.use_cases import (
    ListIncidents,
    ObservedSignal,
    RecordIncident,
    TransitionIncident,
)
from src.domain.incident.value_objects import (
    Fingerprint,
    IncidentStatus,
    Severity,
)
from tests.fakes import InMemoryIncidentRepository


def _observed() -> ObservedSignal:
    return ObservedSignal(
        fingerprint=Fingerprint("checkout", "oom", "prod-ns"),
        source="manual",
        severity=Severity.CRITICAL,
        summary="OOMKilled x5",
    )


@pytest.mark.asyncio
async def test_record_incident_persists_and_returns():
    repo = InMemoryIncidentRepository()

    incident = await RecordIncident(repo).execute("checkout OOM", _observed())

    assert incident.title == "checkout OOM"
    assert await repo.list_all() == [incident]


@pytest.mark.asyncio
async def test_list_incidents_returns_all():
    repo = InMemoryIncidentRepository()
    await RecordIncident(repo).execute("a", _observed())
    await RecordIncident(repo).execute("b", _observed())

    assert len(await ListIncidents(repo).execute()) == 2


@pytest.mark.asyncio
async def test_transition_updates_status():
    repo = InMemoryIncidentRepository()
    incident = await RecordIncident(repo).execute("checkout OOM", _observed())

    updated = await TransitionIncident(repo).execute(
        incident.id, IncidentStatus.RESOLVED
    )

    assert updated is not None
    assert updated.status is IncidentStatus.RESOLVED


@pytest.mark.asyncio
async def test_transition_missing_incident_returns_none():
    repo = InMemoryIncidentRepository()
    ghost = await RecordIncident(repo).execute("ghost", _observed())

    result = await TransitionIncident(InMemoryIncidentRepository()).execute(
        ghost.id, IncidentStatus.RESOLVED
    )

    assert result is None
