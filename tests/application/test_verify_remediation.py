from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.application.remediation.use_cases import (
    ApproveRemediation,
    ExecuteRemediation,
    PlanRemediation,
    VerifyRemediation,
)
from src.domain.auth.identity import Identity
from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import (
    Fingerprint,
    IncidentStatus,
    Severity,
    SignalId,
)
from src.domain.remediation.environment import Environment
from src.domain.remediation.exceptions import InvalidPlanTransitionError
from src.domain.runbook.contract import RunbookId
from src.infrastructure.actuator.k8s_actuator import KubernetesActuator
from src.infrastructure.audit.in_memory_audit_log import InMemoryAuditLog
from src.infrastructure.policy.rule_based_policy import RuleBasedPolicyEngine
from src.infrastructure.remediation.in_memory_circuit_breaker import InMemoryCircuitBreaker
from src.infrastructure.remediation.in_memory_outcome_store import InMemoryOutcomeStore
from src.infrastructure.runbook.static_catalog import StaticRunbookCatalog
from src.infrastructure.runbook.static_registry import StaticRunbookRegistry
from tests.fakes import (
    FakeClusterClient,
    FakeRecoveryProbe,
    InMemoryIncidentRepository,
    InMemoryRemediationPlanRepository,
)

WEB = {"namespace": "default", "deployment": "web"}
FINGERPRINT = "web/crashloop/local"


def _incident() -> Incident:
    signal = Signal(
        id=SignalId(uuid4()),
        fingerprint=Fingerprint("web", "crashloop", "local"),
        source="test",
        severity=Severity.WARNING,
        summary="crashloop",
        observed_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    return Incident.open(title="web crashloop", initial_signal=signal)


async def _executed_plan(plans, incidents, audit):
    incident = _incident()
    await incidents.add(incident)
    actuator = KubernetesActuator(
        StaticRunbookRegistry(FakeClusterClient({("default", "web"): 2})),
        execution_enabled=True,
    )
    plan = await PlanRemediation(
        actuator, RuleBasedPolicyEngine(), StaticRunbookCatalog(), audit, plans
    ).execute(
        Identity("alice", frozenset({"remediation:plan"})),
        incident.id,
        RunbookId("rolling-restart"),
        Environment.LOCAL,
        WEB,
    )
    await ApproveRemediation(plans, audit).execute(
        plan.id, Identity("bob", frozenset({"action:execute:local"}))
    )
    executed = await ExecuteRemediation(
        actuator, plans, audit, InMemoryCircuitBreaker(),
        frozenset({"local"}), frozenset({"rolling-restart"}),
    ).execute(plan.id, Identity("bob", frozenset({"action:execute:local"})))
    return executed, incident.id


@pytest.mark.asyncio
async def test_verify_recovered_resolves_incident_and_records_success():
    plans, incidents, audit = (
        InMemoryRemediationPlanRepository(),
        InMemoryIncidentRepository(),
        InMemoryAuditLog(),
    )
    outcomes = InMemoryOutcomeStore()
    plan, incident_id = await _executed_plan(plans, incidents, audit)

    incident = await VerifyRemediation(
        plans, incidents, audit, outcomes, FakeRecoveryProbe(recovered=True)
    ).execute(plan.id)

    assert incident.status is IncidentStatus.RESOLVED
    assert await outcomes.success_rate(RunbookId("rolling-restart"), FINGERPRINT) == 1.0
    assert [e.event for e in await audit.for_incident(incident_id)][-1] == "verified"


@pytest.mark.asyncio
async def test_verify_not_recovered_escalates_and_records_failure():
    plans, incidents, audit = (
        InMemoryRemediationPlanRepository(),
        InMemoryIncidentRepository(),
        InMemoryAuditLog(),
    )
    outcomes = InMemoryOutcomeStore()
    plan, incident_id = await _executed_plan(plans, incidents, audit)

    incident = await VerifyRemediation(
        plans, incidents, audit, outcomes, FakeRecoveryProbe(recovered=False)
    ).execute(plan.id)

    assert incident.status is IncidentStatus.ESCALATED
    assert await outcomes.success_rate(RunbookId("rolling-restart"), FINGERPRINT) == 0.0
    assert [e.event for e in await audit.for_incident(incident_id)][-1] == "verify_failed"


@pytest.mark.asyncio
async def test_verify_refused_when_plan_not_executed():
    plans, incidents, audit = (
        InMemoryRemediationPlanRepository(),
        InMemoryIncidentRepository(),
        InMemoryAuditLog(),
    )
    incident = _incident()
    await incidents.add(incident)
    actuator = KubernetesActuator(
        StaticRunbookRegistry(FakeClusterClient({("default", "web"): 2})),
        execution_enabled=False,
    )
    plan = await PlanRemediation(
        actuator, RuleBasedPolicyEngine(), StaticRunbookCatalog(), audit, plans
    ).execute(
        Identity("alice", frozenset({"remediation:plan"})),
        incident.id,
        RunbookId("rolling-restart"),
        Environment.LOCAL,
        WEB,
    )  # only PENDING_APPROVAL

    with pytest.raises(InvalidPlanTransitionError):
        await VerifyRemediation(
            plans, incidents, audit, InMemoryOutcomeStore(), FakeRecoveryProbe(True)
        ).execute(plan.id)
