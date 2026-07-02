from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.application.remediation.use_cases import (
    ApproveRemediation,
    ExecuteRemediation,
    PlanRemediation,
)
from src.domain.auth.exceptions import PermissionDeniedError
from src.domain.auth.identity import Identity
from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import Fingerprint, Severity, SignalId
from src.domain.remediation.entities import RemediationStatus
from src.domain.remediation.environment import Environment
from src.domain.remediation.exceptions import (
    ActuationDisabledError,
    CircuitOpenError,
    InvalidPlanTransitionError,
)
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
    InMemoryIncidentRepository,
    InMemoryRemediationPlanRepository,
)

WEB = {"namespace": "default", "deployment": "web"}
ALICE = Identity("alice", frozenset({"remediation:plan"}))
BOB = Identity("bob", frozenset({"action:execute:local"}))
LOCAL_RUNBOOKS = frozenset({"rolling-restart"})
LOCAL_ENVS = frozenset({"local"})


class _Fixture:
    def __init__(self, *, actuation_enabled: bool, max_actions: int = 3) -> None:
        self.cluster = FakeClusterClient({("default", "web"): 2})
        self.plans = InMemoryRemediationPlanRepository()
        self.incidents = InMemoryIncidentRepository()
        self.audit = InMemoryAuditLog()
        self.breaker = InMemoryCircuitBreaker(max_actions=max_actions)
        self.outcomes = InMemoryOutcomeStore()
        registry = StaticRunbookRegistry(self.cluster)
        self.actuator = KubernetesActuator(registry, execution_enabled=actuation_enabled)
        self.planner = PlanRemediation(
            self.actuator, RuleBasedPolicyEngine(), StaticRunbookCatalog(),
            self.audit, self.plans,
        )

    def executor(
        self, envs=LOCAL_ENVS, runbooks=LOCAL_RUNBOOKS
    ) -> ExecuteRemediation:
        return ExecuteRemediation(
            self.actuator, self.plans, self.audit, self.breaker, envs, runbooks,
        )

    async def approved_plan(self, environment=Environment.LOCAL):
        incident = _incident()
        await self.incidents.add(incident)
        plan = await self.planner.execute(
            ALICE, incident.id, RunbookId("rolling-restart"), environment, WEB
        )
        approver = Identity("bob", frozenset({f"action:execute:{environment.value}"}))
        return await ApproveRemediation(self.plans, self.audit).execute(plan.id, approver)


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


@pytest.mark.asyncio
async def test_execute_restarts_and_records_success_when_enabled():
    fx = _Fixture(actuation_enabled=True)
    plan = await fx.approved_plan()

    executed = await fx.executor().execute(plan.id, BOB)

    assert executed.status is RemediationStatus.EXECUTED
    # A real (non-dry-run) patch was issued to the cluster.
    assert fx.cluster.patches[-1][3] is False
    assert [e.event for e in await fx.audit.for_incident(plan.incident_id)][-2:] == [
        "executing",
        "executed",
    ]
    # Execution itself records NO flywheel outcome — that is VerifyRemediation's job.
    assert await fx.outcomes.success_rate(RunbookId("rolling-restart"), "web/crashloop/local") == 0.0


@pytest.mark.asyncio
async def test_execute_requires_env_scoped_permission():
    fx = _Fixture(actuation_enabled=True)
    plan = await fx.approved_plan()
    with pytest.raises(PermissionDeniedError):
        await fx.executor().execute(plan.id, Identity("carol", frozenset()))


@pytest.mark.asyncio
async def test_execute_blocked_when_runbook_not_on_allowlist():
    fx = _Fixture(actuation_enabled=True)
    plan = await fx.approved_plan()
    with pytest.raises(ActuationDisabledError):
        await fx.executor(runbooks=frozenset({"scale-deployment"})).execute(plan.id, BOB)
    # Nothing was patched for real.
    assert all(p[3] is True for p in fx.cluster.patches)


@pytest.mark.asyncio
async def test_execute_refused_before_approval():
    fx = _Fixture(actuation_enabled=True)
    incident = _incident()
    await fx.incidents.add(incident)
    plan = await fx.planner.execute(
        ALICE, incident.id, RunbookId("rolling-restart"), Environment.LOCAL, WEB
    )  # PENDING_APPROVAL, not approved
    with pytest.raises(InvalidPlanTransitionError):
        await fx.executor().execute(plan.id, BOB)


@pytest.mark.asyncio
async def test_circuit_breaker_trips_after_cap():
    fx = _Fixture(actuation_enabled=True, max_actions=1)
    first = await fx.approved_plan()
    await fx.executor().execute(first.id, BOB)  # uses the one allowed action for this incident
    # A second approved plan on the SAME incident is refused by the breaker.
    incident_id = first.incident_id
    second = await fx.planner.execute(
        ALICE, incident_id, RunbookId("rolling-restart"), Environment.LOCAL, WEB
    )
    second = await ApproveRemediation(fx.plans, fx.audit).execute(
        second.id, Identity("bob", frozenset({"action:execute:local"}))
    )
    with pytest.raises(CircuitOpenError):
        await fx.executor().execute(second.id, BOB)
