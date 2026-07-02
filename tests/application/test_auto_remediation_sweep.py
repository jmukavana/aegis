from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.application.remediation.use_cases import (
    AutoRemediate,
    AutoRemediationSweep,
    ExecuteRemediation,
    PlanRemediation,
    VerifyRemediation,
)
from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import Fingerprint, Severity, SignalId
from src.domain.remediation.entities import RemediationStatus
from src.domain.remediation.environment import Environment
from src.domain.runbook.contract import RunbookId
from src.infrastructure.actuator.k8s_actuator import KubernetesActuator
from src.infrastructure.audit.in_memory_audit_log import InMemoryAuditLog
from src.infrastructure.policy.rule_based_policy import AutoThreshold, RuleBasedPolicyEngine
from src.infrastructure.remediation.in_memory_circuit_breaker import InMemoryCircuitBreaker
from src.infrastructure.remediation.in_memory_outcome_store import InMemoryOutcomeStore
from src.infrastructure.remediation.rule_based_selector import RuleBasedRemediationSelector
from src.infrastructure.runbook.static_catalog import StaticRunbookCatalog
from src.infrastructure.runbook.static_registry import StaticRunbookRegistry
from tests.fakes import (
    FakeClusterClient,
    FakeRecoveryProbe,
    InMemoryIncidentRepository,
    InMemoryRemediationPlanRepository,
)


def _incident(failure_class: str) -> Incident:
    signal = Signal(
        id=SignalId(uuid4()),
        fingerprint=Fingerprint("checkout", failure_class, "default"),
        source="kubernetes",
        severity=Severity.CRITICAL,
        summary="boom",
        observed_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    return Incident.open(title="x", initial_signal=signal)


def _sweep(incidents, plans, outcomes):
    actuator = KubernetesActuator(
        StaticRunbookRegistry(FakeClusterClient({("default", "checkout"): 2})),
        execution_enabled=True,
    )
    policy = RuleBasedPolicyEngine(
        allow_auto=True,
        auto_thresholds={
            Environment.LOCAL: AutoThreshold(
                min_success_rate=0.8, min_samples=2, max_blast_radius=99
            )
        },
    )
    audit = InMemoryAuditLog()
    auto = AutoRemediate(
        PlanRemediation(actuator, policy, StaticRunbookCatalog(), audit, plans, incidents, outcomes),
        ExecuteRemediation(actuator, plans, audit, InMemoryCircuitBreaker(),
                           frozenset({"local"}), frozenset({"rolling-restart"})),
        VerifyRemediation(plans, incidents, audit, outcomes, FakeRecoveryProbe(True)),
    )
    return AutoRemediationSweep(
        incidents, plans, RuleBasedRemediationSelector(Environment.LOCAL), auto
    )


@pytest.mark.asyncio
async def test_sweep_autoheals_earned_incident_and_is_idempotent():
    incidents, plans, outcomes = (
        InMemoryIncidentRepository(),
        InMemoryRemediationPlanRepository(),
        InMemoryOutcomeStore(),
    )
    await outcomes.record(RunbookId("rolling-restart"), "checkout/crashloop/default", True)
    await outcomes.record(RunbookId("rolling-restart"), "checkout/crashloop/default", True)
    incident = _incident("crashloop")
    await incidents.add(incident)

    acted = await _sweep(incidents, plans, outcomes).run()

    assert acted == [incident.id]
    assert await plans.exists_for_incident(incident.id)
    # Second sweep does nothing — the incident already has a plan (no re-plan spam).
    assert await _sweep(incidents, plans, outcomes).run() == []


@pytest.mark.asyncio
async def test_sweep_skips_incident_with_no_matching_runbook():
    incidents, plans, outcomes = (
        InMemoryIncidentRepository(),
        InMemoryRemediationPlanRepository(),
        InMemoryOutcomeStore(),
    )
    await incidents.add(_incident("latency"))  # unmapped failure class

    assert await _sweep(incidents, plans, outcomes).run() == []


@pytest.mark.asyncio
async def test_sweep_leaves_unearned_incident_as_pending_plan():
    incidents, plans, outcomes = (
        InMemoryIncidentRepository(),
        InMemoryRemediationPlanRepository(),
        InMemoryOutcomeStore(),
    )
    incident = _incident("crashloop")  # no track record -> not auto-eligible
    await incidents.add(incident)

    acted = await _sweep(incidents, plans, outcomes).run()

    assert acted == [incident.id]
    plan_id = next(iter(plans._plans))  # noqa: SLF001 - test introspection
    assert plans._plans[plan_id].status is RemediationStatus.PENDING_APPROVAL