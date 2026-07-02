from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.application.analysis.use_cases import ExplainIncident
from src.domain.analysis.gateway import IncidentAnalysisGateway
from src.domain.analysis.memory import RecalledIncident
from src.domain.analysis.models import IncidentAnalysis, RemediationProposal
from src.domain.incident.entities import Incident, Signal
from src.domain.incident.exceptions import IncidentNotFoundError
from src.domain.incident.value_objects import Fingerprint, IncidentId, Severity, SignalId
from src.domain.runbook.contract import RunbookId
from src.infrastructure.runbook.static_catalog import StaticRunbookCatalog
from tests.fakes import InMemoryIncidentMemory, InMemoryIncidentRepository


class FakeAnalysisGateway(IncidentAnalysisGateway):
    def __init__(self, analysis: IncidentAnalysis) -> None:
        self._analysis = analysis
        self.seen_similar: list[RecalledIncident] | None = None

    async def explain(self, incident, catalog, similar) -> IncidentAnalysis:
        self.seen_similar = similar
        return self._analysis


def _incident() -> Incident:
    signal = Signal(
        id=SignalId(uuid4()),
        fingerprint=Fingerprint("checkout", "oom", "prod"),
        source="test",
        severity=Severity.CRITICAL,
        summary="OOMKilled",
        observed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    return Incident.open(title="checkout: oom", initial_signal=signal)


def _analysis(*proposals: RemediationProposal) -> IncidentAnalysis:
    return IncidentAnalysis(summary="s", root_cause="rc", proposals=tuple(proposals))


@pytest.mark.asyncio
async def test_explain_returns_analysis_for_known_incident():
    repo = InMemoryIncidentRepository()
    incident = _incident()
    await repo.add(incident)
    gateway = FakeAnalysisGateway(
        _analysis(
            RemediationProposal(RunbookId("rolling-restart"), "ns=prod", "reclaim mem", 0.8)
        )
    )

    result = await ExplainIncident(gateway, repo, StaticRunbookCatalog()).execute(
        incident.id
    )

    assert result.root_cause == "rc"
    assert len(result.proposals) == 1


@pytest.mark.asyncio
async def test_explain_drops_proposals_referencing_unknown_runbooks():
    repo = InMemoryIncidentRepository()
    incident = _incident()
    await repo.add(incident)
    gateway = FakeAnalysisGateway(
        _analysis(
            RemediationProposal(RunbookId("rolling-restart"), "", "known", 0.9),
            RemediationProposal(RunbookId("delete-prod-database"), "", "hallucinated", 0.9),
        )
    )

    result = await ExplainIncident(gateway, repo, StaticRunbookCatalog()).execute(
        incident.id
    )

    assert [str(p.runbook_id) for p in result.proposals] == ["rolling-restart"]


@pytest.mark.asyncio
async def test_explain_passes_recalled_incidents_to_gateway_and_remembers():
    repo = InMemoryIncidentRepository()
    incident = _incident()
    await repo.add(incident)
    gateway = FakeAnalysisGateway(
        _analysis(RemediationProposal(RunbookId("rolling-restart"), "", "known", 0.9))
    )
    recalled = RecalledIncident(
        title="checkout: prior oom",
        fingerprint="checkout/oom/prod",
        root_cause="leak in cart serializer",
        similarity=0.91,
    )
    memory = InMemoryIncidentMemory(recalls=[recalled])

    result = await ExplainIncident(
        gateway, repo, StaticRunbookCatalog(), memory
    ).execute(incident.id)

    # The recalled past incident was handed to the gateway as prompt context...
    assert gateway.seen_similar == [recalled]
    # ...and this incident + its analysis were remembered for future recalls.
    assert len(memory.remembered) == 1
    remembered_incident, remembered_analysis = memory.remembered[0]
    assert remembered_incident.id == incident.id
    assert remembered_analysis is result


@pytest.mark.asyncio
async def test_explain_without_memory_passes_empty_similar():
    repo = InMemoryIncidentRepository()
    incident = _incident()
    await repo.add(incident)
    gateway = FakeAnalysisGateway(_analysis())

    await ExplainIncident(gateway, repo, StaticRunbookCatalog()).execute(incident.id)

    assert gateway.seen_similar == []


@pytest.mark.asyncio
async def test_explain_raises_for_missing_incident():
    repo = InMemoryIncidentRepository()
    gateway = FakeAnalysisGateway(_analysis())

    with pytest.raises(IncidentNotFoundError):
        await ExplainIncident(gateway, repo, StaticRunbookCatalog()).execute(
            IncidentId(uuid4())
        )
