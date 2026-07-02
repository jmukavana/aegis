import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.entities import (
    Approval,
    RemediationPlan,
    RemediationStatus,
)
from src.domain.remediation.environment import Environment
from src.domain.remediation.policy import PolicyDecision, PolicyOutcome
from src.domain.remediation.repository import RemediationPlanRepository
from src.domain.remediation.value_objects import RemediationPlanId
from src.domain.runbook.contract import DryRunResult, RiskTier, RunbookId


class RemediationPlanModel(SQLModel, table=True):
    __tablename__ = "remediation_plans"

    id: UUID = Field(primary_key=True)
    incident_id: UUID = Field(index=True)
    runbook_id: str
    environment: str
    risk_tier: str
    requested_by: str
    params_json: str
    confidence: float
    status: str
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    # Flattened dry-run + policy decision (nullable until those steps run).
    dry_run_effect: str | None = None
    dry_run_reversible: bool | None = None
    decision_outcome: str | None = None
    decision_reasons_json: str | None = None
    required_permission: str | None = None
    # Flattened approval (nullable until approved).
    approver: str | None = None
    approval_permission: str | None = None
    approved_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )

    def to_entity(self) -> RemediationPlan:
        dry_run = (
            DryRunResult(self.dry_run_effect, bool(self.dry_run_reversible))
            if self.dry_run_effect is not None
            else None
        )
        decision = (
            PolicyDecision(
                PolicyOutcome(self.decision_outcome),
                tuple(json.loads(self.decision_reasons_json or "[]")),
                self.required_permission,
            )
            if self.decision_outcome is not None
            else None
        )
        approval = (
            Approval(self.approver, self.approval_permission or "", self.approved_at)
            if self.approver is not None and self.approved_at is not None
            else None
        )
        return RemediationPlan(
            id=RemediationPlanId(self.id),
            incident_id=IncidentId(self.incident_id),
            runbook_id=RunbookId(self.runbook_id),
            environment=Environment(self.environment),
            risk_tier=RiskTier(self.risk_tier),
            requested_by=self.requested_by,
            params=json.loads(self.params_json),
            confidence=self.confidence,
            status=RemediationStatus(self.status),
            created_at=self.created_at,
            dry_run=dry_run,
            decision=decision,
            approval=approval,
        )

    @classmethod
    def from_entity(cls, plan: RemediationPlan) -> "RemediationPlanModel":
        return cls(
            id=plan.id,
            incident_id=plan.incident_id,
            runbook_id=str(plan.runbook_id),
            environment=plan.environment.value,
            risk_tier=plan.risk_tier.value,
            requested_by=plan.requested_by,
            params_json=json.dumps(plan.params),
            confidence=plan.confidence,
            status=plan.status.value,
            created_at=plan.created_at,
            dry_run_effect=plan.dry_run.predicted_effect if plan.dry_run else None,
            dry_run_reversible=plan.dry_run.reversible if plan.dry_run else None,
            decision_outcome=plan.decision.outcome.value if plan.decision else None,
            decision_reasons_json=(
                json.dumps(list(plan.decision.reasons)) if plan.decision else None
            ),
            required_permission=(
                plan.decision.required_permission if plan.decision else None
            ),
            approver=plan.approval.approver if plan.approval else None,
            approval_permission=(
                plan.approval.permission_used if plan.approval else None
            ),
            approved_at=plan.approval.approved_at if plan.approval else None,
        )


class SqlAlchemyRemediationPlanRepository(RemediationPlanRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, plan: RemediationPlan) -> None:
        # Upsert: plans advance through their lifecycle in place, keyed by id.
        await self._session.merge(RemediationPlanModel.from_entity(plan))
        await self._session.commit()

    async def get(self, plan_id: RemediationPlanId) -> RemediationPlan | None:
        model = await self._session.get(RemediationPlanModel, plan_id)
        return model.to_entity() if model else None

    async def exists_for_incident(self, incident_id: IncidentId) -> bool:
        statement = (
            select(RemediationPlanModel.id)
            .where(col(RemediationPlanModel.incident_id) == incident_id)
            .limit(1)
        )
        result = await self._session.exec(statement)
        return result.first() is not None
