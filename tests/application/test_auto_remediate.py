from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.application.remediation.use_cases import (
    AutoRemediate,
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


def _auto_remediator(*, recovered: bool, plans, incidents, audit, outcomes):
    actuator = KubernetesActuator(
        StaticRunbookRegistry(FakeClusterClient({("default", "web"): 2})),
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
    return AutoRemediate(
        PlanRemediation(
            actuator, policy, StaticRunbookCatalog(), audit, plans, incidents, outcomes
        ),
        ExecuteRemediation(
            actuator, plans, audit, InMemoryCircuitBreaker(),
            frozenset({"local"}), frozenset({"rolling-restart"}),
        ),
        VerifyRemediation(plans, incidents, audit, outcomes, FakeRecoveryProbe(recovered)),
    )


@pytest.mark.asyncio
async def test_auto_executes_and_verifies_once_trust_is_earned():
    plans, incidents, audit, outcomes = (
        InMemoryRemediationPlanRepository(),
        InMemoryIncidentRepository(),
        InMemoryAuditLog(),
        InMemoryOutcomeStore(),
    )
    # Seed an earned track record: 2 prior verified successes for this (runbook, fingerprint).
    await outcomes.record(RunbookId("rolling-restart"), FINGERPRINT, True)
    await outcomes.record(RunbookId("rolling-restart"), FINGERPRINT, True)
    incident = _incident()
    await incidents.add(incident)

    plan = await _auto_remediator(
        recovered=True, plans=plans, incidents=incidents, audit=audit, outcomes=outcomes
    ).execute(incident.id, RunbookId("rolling-restart"), Environment.LOCAL, WEB)

    assert plan.status is RemediationStatus.EXECUTED  # ran with no human approval
    events = [e.event for e in await audit.for_incident(incident.id)]
    assert "auto_approved" in events and "executed" in events and "verified" in events
    assert "approved" not in events  # no human approval step


@pytest.mark.asyncio
async def test_without_track_record_it_waits_for_a_human():
    plans, incidents, audit, outcomes = (
        InMemoryRemediationPlanRepository(),
        InMemoryIncidentRepository(),
        InMemoryAuditLog(),
        InMemoryOutcomeStore(),
    )
    incident = _incident()
    await incidents.add(incident)

    plan = await _auto_remediator(
        recovered=True, plans=plans, incidents=incidents, audit=audit, outcomes=outcomes
    ).execute(incident.id, RunbookId("rolling-restart"), Environment.LOCAL, WEB)

    # No history -> policy requires approval -> AutoRemediate leaves it pending, nothing ran.
    assert plan.status is RemediationStatus.PENDING_APPROVAL
    assert "executed" not in [e.event for e in await audit.for_incident(incident.id)]
