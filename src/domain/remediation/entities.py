from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.environment import Environment
from src.domain.remediation.exceptions import (
    InvalidPlanTransitionError,
    SelfApprovalError,
)
from src.domain.remediation.policy import PolicyDecision
from src.domain.remediation.value_objects import RemediationPlanId
from src.domain.runbook.contract import DryRunResult, RiskTier, RunbookId


@dataclass(frozen=True)
class Approval:
    """A recorded human approval of a plan — who, with which permission, when."""

    approver: str
    permission_used: str
    approved_at: datetime


class RemediationStatus(str, Enum):
    """Lifecycle of a remediation plan. Phase 3 stops at EXECUTED (human-approved); the terminal
    branches DENIED/FAILED/ROLLED_BACK capture the unhappy paths."""

    DRAFTED = "drafted"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    DENIED = "denied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class RemediationPlan:
    """A concrete, validated remediation bound to one incident + environment (Phase 3).

    Turns a Phase 2 advisory `RemediationProposal` into something a policy gate can rule on and
    a human can approve: a specific runbook + params + environment, carrying the dry-run's
    predicted effect and the policy decision. Frozen with withers, like the `Incident` aggregate.
    Nothing here executes — status only advances past APPROVED once the guarded executor runs.
    """

    id: RemediationPlanId
    incident_id: IncidentId
    runbook_id: RunbookId
    environment: Environment
    risk_tier: RiskTier
    requested_by: str
    params: dict = field(default_factory=dict)
    confidence: float = 1.0
    status: RemediationStatus = RemediationStatus.DRAFTED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dry_run: DryRunResult | None = None
    decision: PolicyDecision | None = None
    approval: Approval | None = None

    @classmethod
    def draft(
        cls,
        incident_id: IncidentId,
        runbook_id: RunbookId,
        environment: Environment,
        risk_tier: RiskTier,
        requested_by: str,
        params: dict,
        confidence: float = 1.0,
    ) -> "RemediationPlan":
        return cls(
            id=RemediationPlanId(uuid4()),
            incident_id=incident_id,
            runbook_id=runbook_id,
            environment=environment,
            risk_tier=risk_tier,
            requested_by=requested_by,
            params=dict(params),
            confidence=confidence,
        )

    def with_dry_run(self, dry_run: DryRunResult) -> "RemediationPlan":
        return replace(self, dry_run=dry_run)

    def with_decision(self, decision: PolicyDecision) -> "RemediationPlan":
        return replace(self, decision=decision)

    def to_status(self, status: RemediationStatus) -> "RemediationPlan":
        return replace(self, status=status)

    def approve(
        self, approver: str, permission_used: str, at: datetime
    ) -> "RemediationPlan":
        """Record a human approval. Enforces the two domain invariants of approval: the plan
        must be awaiting approval, and the approver cannot be the requester (segregation of duty).
        The RBAC permission check itself lives in the application layer (it needs the identity)."""
        if self.status is not RemediationStatus.PENDING_APPROVAL:
            raise InvalidPlanTransitionError(
                f"cannot approve a plan in status '{self.status.value}'"
            )
        if approver == self.requested_by:
            raise SelfApprovalError(
                f"{approver} cannot approve their own plan {self.id}"
            )
        return replace(
            self,
            status=RemediationStatus.APPROVED,
            approval=Approval(approver, permission_used, at),
        )
