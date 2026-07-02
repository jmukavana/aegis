from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.correlation.use_cases import CorrelateSignal
from src.domain.change.change_log import ChangeEvent
from src.domain.incident.entities import Signal
from src.domain.incident.value_objects import Fingerprint, Severity, SignalId
from tests.fakes import FakeChangeLog, FakeServiceGraph, InMemoryIncidentRepository

BASE = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _signal(service: str, failure: str, *, at: datetime, resource: str = "prod") -> Signal:
    return Signal(
        id=SignalId(uuid4()),
        fingerprint=Fingerprint(service, failure, resource),
        source="test",
        severity=Severity.CRITICAL,
        summary=f"{service} {failure}",
        observed_at=at,
    )


def _engine(
    repo: InMemoryIncidentRepository,
    *,
    graph: FakeServiceGraph | None = None,
    changes: FakeChangeLog | None = None,
) -> CorrelateSignal:
    return CorrelateSignal(
        repo, graph or FakeServiceGraph(), changes or FakeChangeLog()
    )


@pytest.mark.asyncio
async def test_first_signal_opens_a_new_incident():
    repo = InMemoryIncidentRepository()

    result = await _engine(repo).execute(_signal("checkout", "oom", at=BASE))

    assert result.newly_opened is True
    assert len(await repo.list_open()) == 1


@pytest.mark.asyncio
async def test_same_fingerprint_within_window_deduplicates():
    repo = InMemoryIncidentRepository()
    engine = _engine(repo)

    first = await engine.execute(_signal("checkout", "oom", at=BASE))
    second = await engine.execute(
        _signal("checkout", "oom", at=BASE + timedelta(minutes=2))
    )

    assert second.newly_opened is False
    assert second.incident.id == first.incident.id
    assert len(second.incident.signals) == 2


@pytest.mark.asyncio
async def test_same_fingerprint_outside_window_opens_new_incident():
    repo = InMemoryIncidentRepository()
    engine = _engine(repo)

    await engine.execute(_signal("checkout", "oom", at=BASE))
    later = await engine.execute(
        _signal("checkout", "oom", at=BASE + timedelta(minutes=30))
    )

    assert later.newly_opened is True
    assert len(await repo.list_all()) == 2


@pytest.mark.asyncio
async def test_downstream_signal_attaches_to_upstream_incident():
    # checkout depends on payments; a payments incident should absorb checkout's fallout.
    repo = InMemoryIncidentRepository()
    graph = FakeServiceGraph({"checkout": frozenset({"payments"})})
    engine = _engine(repo, graph=graph)

    root = await engine.execute(_signal("payments", "down", at=BASE))
    downstream = await engine.execute(
        _signal("checkout", "errors", at=BASE + timedelta(minutes=1))
    )

    assert downstream.newly_opened is False
    assert downstream.incident.id == root.incident.id
    assert len(await repo.list_open()) == 1


@pytest.mark.asyncio
async def test_unrelated_services_stay_separate():
    repo = InMemoryIncidentRepository()
    engine = _engine(repo, graph=FakeServiceGraph())

    await engine.execute(_signal("payments", "down", at=BASE))
    other = await engine.execute(
        _signal("search", "latency", at=BASE + timedelta(minutes=1))
    )

    assert other.newly_opened is True
    assert len(await repo.list_open()) == 2


@pytest.mark.asyncio
async def test_new_incident_is_attributed_to_a_recent_change():
    repo = InMemoryIncidentRepository()
    changes = FakeChangeLog(
        [
            ChangeEvent(
                service="checkout",
                kind="deploy",
                description="deployed v42",
                occurred_at=BASE - timedelta(minutes=3),
            )
        ]
    )
    engine = _engine(repo, changes=changes)

    result = await engine.execute(_signal("checkout", "errors", at=BASE))

    assert result.incident.suspected_change == "deployed v42"
