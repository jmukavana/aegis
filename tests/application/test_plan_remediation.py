from uuid import uuid4

import pytest

from src.application.remediation.use_cases import ApproveRemediation, PlanRemediation
from src.domain.auth.exceptions import PermissionDeniedError
from src.domain.auth.identity import Identity
from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.entities import RemediationStatus
from src.domain.remediation.environment import Environment
from src.domain.remediation.exceptions import (
    RemediationPlanNotFoundError,
    SelfApprovalError,
)
from src.domain.remediation.policy import PolicyOutcome
from src.domain.remediation.value_objects import RemediationPlanId
from src.domain.runbook.contract import RunbookId
from src.domain.runbook.exceptions import RunbookNotFoundError
from src.infrastructure.actuator.k8s_actuator import KubernetesActuator
from src.infrastructure.audit.in_memory_audit_log import InMemoryAuditLog
from src.infrastructure.policy.rule_based_policy import RuleBasedPolicyEngine
from src.infrastructure.runbook.static_catalog import StaticRunbookCatalog
from src.infrastructure.runbook.static_registry import StaticRunbookRegistry
from tests.fakes import FakeClusterClient, InMemoryRemediationPlanRepository

WEB = {"namespace": "default", "deployment": "web"}
PLANNER = Identity("alice", frozenset({"remediation:plan"}))


def _pipeline(audit, plans) -> PlanRemediation:
    registry = StaticRunbookRegistry(FakeClusterClient({("default", "web"): 2}))
    # Execution stays OFF — planning only ever dry-runs.
    actuator = KubernetesActuator(registry, execution_enabled=False)
    return PlanRemediation(
        actuator, RuleBasedPolicyEngine(), StaticRunbookCatalog(), audit, plans
    )


@pytest.mark.asyncio
async def test_plan_low_risk_staging_awaits_approval_and_audits_every_step():
    audit, plans = InMemoryAuditLog(), InMemoryRemediationPlanRepository()
    incident_id = IncidentId(uuid4())

    plan = await _pipeline(audit, plans).execute(
        PLANNER, incident_id, RunbookId("rolling-restart"), Environment.STAGING, WEB
    )

    assert plan.status is RemediationStatus.PENDING_APPROVAL
    assert plan.decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert plan.decision.required_permission == "action:execute:staging"
    assert plan.requested_by == "alice"
    assert plan.dry_run is not None and "web" in plan.dry_run.predicted_effect
    assert await plans.get(plan.id) is not None  # persisted
    trail = await audit.for_incident(incident_id)
    assert [e.event for e in trail] == ["drafted", "dry_run", "policy_decision"]


@pytest.mark.asyncio
async def test_planning_requires_plan_permission():
    with pytest.raises(PermissionDeniedError):
        await _pipeline(InMemoryAuditLog(), InMemoryRemediationPlanRepository()).execute(
            Identity("mallory", frozenset()),
            IncidentId(uuid4()),
            RunbookId("rolling-restart"),
            Environment.STAGING,
            WEB,
        )


@pytest.mark.asyncio
async def test_plan_in_prod_is_denied():
    audit, plans = InMemoryAuditLog(), InMemoryRemediationPlanRepository()
    plan = await _pipeline(audit, plans).execute(
        PLANNER, IncidentId(uuid4()), RunbookId("rolling-restart"), Environment.PROD, WEB
    )
    assert plan.status is RemediationStatus.DENIED
    assert plan.decision.outcome is PolicyOutcome.DENY


@pytest.mark.asyncio
async def test_plan_rejects_runbook_absent_from_catalog():
    with pytest.raises(RunbookNotFoundError):
        await _pipeline(InMemoryAuditLog(), InMemoryRemediationPlanRepository()).execute(
            PLANNER, IncidentId(uuid4()), RunbookId("delete-prod-database"),
            Environment.LOCAL, {},
        )


@pytest.mark.asyncio
async def test_plan_rejects_catalog_runbook_without_executable():
    # `increase-memory-limit` is in the Phase 2 catalog but has no Phase 3 executable yet.
    with pytest.raises(RunbookNotFoundError):
        await _pipeline(InMemoryAuditLog(), InMemoryRemediationPlanRepository()).execute(
            PLANNER, IncidentId(uuid4()), RunbookId("increase-memory-limit"),
            Environment.STAGING, {**WEB, "memory_limit": "1Gi"},
        )


# --- approval ---------------------------------------------------------------------------

async def _pending_plan(audit, plans):
    return await _pipeline(audit, plans).execute(
        PLANNER, IncidentId(uuid4()), RunbookId("rolling-restart"), Environment.STAGING, WEB
    )


@pytest.mark.asyncio
async def test_approval_by_authorized_other_person_succeeds_and_audits():
    audit, plans = InMemoryAuditLog(), InMemoryRemediationPlanRepository()
    plan = await _pending_plan(audit, plans)
    approver = Identity("bob", frozenset({"action:execute:staging"}))

    approved = await ApproveRemediation(plans, audit).execute(plan.id, approver)

    assert approved.status is RemediationStatus.APPROVED
    assert approved.approval.approver == "bob"
    assert [e.event for e in await audit.for_incident(plan.incident_id)][-1] == "approved"


@pytest.mark.asyncio
async def test_approval_without_required_permission_is_denied():
    audit, plans = InMemoryAuditLog(), InMemoryRemediationPlanRepository()
    plan = await _pending_plan(audit, plans)
    with pytest.raises(PermissionDeniedError):
        await ApproveRemediation(plans, audit).execute(
            plan.id, Identity("bob", frozenset({"action:execute:local"}))
        )


@pytest.mark.asyncio
async def test_requester_cannot_approve_own_plan():
    audit, plans = InMemoryAuditLog(), InMemoryRemediationPlanRepository()
    plan = await _pending_plan(audit, plans)
    # alice drafted it, and even with the permission she may not self-approve.
    with pytest.raises(SelfApprovalError):
        await ApproveRemediation(plans, audit).execute(
            plan.id, Identity("alice", frozenset({"action:execute:staging"}))
        )


@pytest.mark.asyncio
async def test_approving_unknown_plan_raises():
    with pytest.raises(RemediationPlanNotFoundError):
        await ApproveRemediation(
            InMemoryRemediationPlanRepository(), InMemoryAuditLog()
        ).execute(RemediationPlanId(uuid4()), PLANNER)
