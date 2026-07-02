from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from opentelemetry import trace
from pydantic import BaseModel

from src.application.remediation.use_cases import (
    ApproveRemediation,
    AutoRemediate,
    ExecuteRemediation,
    PlanRemediation,
    VerifyRemediation,
)
from src.domain.actuator.port import Actuator
from src.domain.auth.exceptions import PermissionDeniedError
from src.domain.auth.identity import Identity
from src.domain.incident.exceptions import IncidentNotFoundError
from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.entities import RemediationPlan
from src.domain.remediation.environment import Environment
from src.domain.remediation.exceptions import (
    ActuationDisabledError,
    CircuitOpenError,
    InvalidPlanTransitionError,
    RemediationPlanNotFoundError,
    SelfApprovalError,
)
from src.domain.remediation.policy import PolicyEngine
from src.domain.remediation.recovery import RecoveryProbe
from src.domain.remediation.value_objects import RemediationPlanId
from src.domain.runbook.contract import RunbookId
from src.domain.runbook.exceptions import RunbookNotFoundError
from src.domain.runbook.spec import RunbookCatalog
from src.domain.topology.service_graph import ServiceGraph
from src.infrastructure.persistence.audit_repository import SqlAlchemyAuditLog
from src.infrastructure.persistence.circuit_breaker_repository import (
    SqlAlchemyCircuitBreaker,
)
from src.infrastructure.persistence.incident_repository import (
    SqlAlchemyIncidentRepository,
)
from src.infrastructure.persistence.outcome_repository import SqlAlchemyOutcomeStore
from src.infrastructure.persistence.remediation_repository import (
    SqlAlchemyRemediationPlanRepository,
)

tracer = trace.get_tracer(__name__)


class PlanRequest(BaseModel):
    runbook_id: str
    environment: Environment
    params: dict = {}
    confidence: float = 1.0


def _serialize(plan: RemediationPlan) -> dict:
    return {
        "id": str(plan.id),
        "incident_id": str(plan.incident_id),
        "runbook_id": str(plan.runbook_id),
        "environment": plan.environment.value,
        "risk_tier": plan.risk_tier.value,
        "status": plan.status.value,
        "requested_by": plan.requested_by,
        "params": plan.params,
        "dry_run": (
            {"predicted_effect": plan.dry_run.predicted_effect, "reversible": plan.dry_run.reversible}
            if plan.dry_run
            else None
        ),
        "decision": (
            {
                "outcome": plan.decision.outcome.value,
                "reasons": list(plan.decision.reasons),
                "required_permission": plan.decision.required_permission,
            }
            if plan.decision
            else None
        ),
        "approval": (
            {"approver": plan.approval.approver, "permission_used": plan.approval.permission_used}
            if plan.approval
            else None
        ),
    }


def build_remediation_router(
    logger,
    get_session,
    get_identity,
    actuator: Actuator,
    policy: PolicyEngine,
    catalog: RunbookCatalog,
    recovery_probe: RecoveryProbe,
    service_graph: ServiceGraph,
    enabled_environments: frozenset[str],
    enabled_runbooks: frozenset[str],
    breaker_max_actions: int,
    breaker_window: timedelta,
) -> APIRouter:
    router = APIRouter(tags=["remediation"])

    # Durable, session-bound state (Phase 4c): the flywheel + breaker live in Postgres, so they
    # survive restarts/replicas. Built per request like the repositories, not app singletons.
    def _outcomes(session) -> SqlAlchemyOutcomeStore:
        return SqlAlchemyOutcomeStore(session)

    def _breaker(session) -> SqlAlchemyCircuitBreaker:
        return SqlAlchemyCircuitBreaker(session, breaker_max_actions, breaker_window)

    @router.post("/incidents/{incident_id}/remediations", status_code=201)
    async def plan_remediation(
        incident_id: UUID,
        body: PlanRequest,
        identity: Identity = Depends(get_identity),
        session=Depends(get_session),
    ):
        use_case = PlanRemediation(
            actuator,
            policy,
            catalog,
            SqlAlchemyAuditLog(session),
            SqlAlchemyRemediationPlanRepository(session),
            SqlAlchemyIncidentRepository(session),
            _outcomes(session),
            service_graph,
        )
        with tracer.start_as_current_span("plan_remediation") as span:
            span.set_attribute("incident.id", str(incident_id))
            span.set_attribute("runbook.id", body.runbook_id)
            try:
                plan = await use_case.execute(
                    identity,
                    IncidentId(incident_id),
                    RunbookId(body.runbook_id),
                    body.environment,
                    body.params,
                    body.confidence,
                )
            except PermissionDeniedError as error:
                raise HTTPException(status_code=403, detail=str(error)) from error
            except RunbookNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            span.set_attribute("plan.status", plan.status.value)
        logger.info(
            "Planned %s for incident %s -> %s", body.runbook_id, incident_id, plan.status.value
        )
        return _serialize(plan)

    @router.post("/incidents/{incident_id}/auto-remediate", status_code=201)
    async def auto_remediate(
        incident_id: UUID, body: PlanRequest, session=Depends(get_session)
    ):
        # No identity: autonomy is authorised by the policy's ALLOW_AUTO decision, not a human.
        plans = SqlAlchemyRemediationPlanRepository(session)
        audit = SqlAlchemyAuditLog(session)
        incidents = SqlAlchemyIncidentRepository(session)
        use_case = AutoRemediate(
            PlanRemediation(
                actuator, policy, catalog, audit, plans, incidents,
                _outcomes(session), service_graph,
            ),
            ExecuteRemediation(
                actuator, plans, audit, _breaker(session),
                enabled_environments, enabled_runbooks,
            ),
            VerifyRemediation(plans, incidents, audit, _outcomes(session), recovery_probe),
        )
        with tracer.start_as_current_span("auto_remediate") as span:
            span.set_attribute("incident.id", str(incident_id))
            try:
                plan = await use_case.execute(
                    IncidentId(incident_id),
                    RunbookId(body.runbook_id),
                    body.environment,
                    body.params,
                    body.confidence,
                )
            except RunbookNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            span.set_attribute("plan.status", plan.status.value)
            span.set_attribute(
                "plan.autonomous",
                bool(plan.decision and plan.decision.outcome.value == "allow_auto"),
            )
        logger.info(
            "Auto-remediate incident %s -> %s", incident_id, plan.status.value
        )
        return _serialize(plan)

    @router.post("/remediations/{plan_id}/approve")
    async def approve_remediation(
        plan_id: UUID,
        identity: Identity = Depends(get_identity),
        session=Depends(get_session),
    ):
        use_case = ApproveRemediation(
            SqlAlchemyRemediationPlanRepository(session), SqlAlchemyAuditLog(session)
        )
        with tracer.start_as_current_span("approve_remediation") as span:
            span.set_attribute("plan.id", str(plan_id))
            try:
                plan = await use_case.execute(RemediationPlanId(plan_id), identity)
            except RemediationPlanNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except PermissionDeniedError as error:
                raise HTTPException(status_code=403, detail=str(error)) from error
            except (InvalidPlanTransitionError, SelfApprovalError) as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        logger.info("Plan %s approved by %s", plan_id, identity.subject)
        return _serialize(plan)

    @router.post("/remediations/{plan_id}/execute")
    async def execute_remediation(
        plan_id: UUID,
        identity: Identity = Depends(get_identity),
        session=Depends(get_session),
    ):
        use_case = ExecuteRemediation(
            actuator,
            SqlAlchemyRemediationPlanRepository(session),
            SqlAlchemyAuditLog(session),
            _breaker(session),
            enabled_environments,
            enabled_runbooks,
        )
        with tracer.start_as_current_span("execute_remediation") as span:
            span.set_attribute("plan.id", str(plan_id))
            try:
                plan = await use_case.execute(RemediationPlanId(plan_id), identity)
            except RemediationPlanNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except PermissionDeniedError as error:
                raise HTTPException(status_code=403, detail=str(error)) from error
            except (InvalidPlanTransitionError, ActuationDisabledError) as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except CircuitOpenError as error:
                raise HTTPException(status_code=429, detail=str(error)) from error
            span.set_attribute("plan.status", plan.status.value)
        logger.info("Plan %s executed by %s -> %s", plan_id, identity.subject, plan.status.value)
        return _serialize(plan)

    @router.post("/remediations/{plan_id}/verify")
    async def verify_remediation(plan_id: UUID, session=Depends(get_session)):
        use_case = VerifyRemediation(
            SqlAlchemyRemediationPlanRepository(session),
            SqlAlchemyIncidentRepository(session),
            SqlAlchemyAuditLog(session),
            _outcomes(session),
            recovery_probe,
        )
        with tracer.start_as_current_span("verify_remediation") as span:
            span.set_attribute("plan.id", str(plan_id))
            try:
                incident = await use_case.execute(RemediationPlanId(plan_id))
            except RemediationPlanNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except IncidentNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except InvalidPlanTransitionError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            recovered = incident.status.value == "resolved"
            span.set_attribute("incident.recovered", recovered)
        logger.info("Verified plan %s -> incident %s", plan_id, incident.status.value)
        return {
            "plan_id": str(plan_id),
            "incident_id": str(incident.id),
            "recovered": recovered,
            "incident_status": incident.status.value,
        }

    @router.get("/remediations/{plan_id}")
    async def get_remediation(plan_id: UUID, session=Depends(get_session)):
        repo = SqlAlchemyRemediationPlanRepository(session)
        plan = await repo.get(RemediationPlanId(plan_id))
        if plan is None:
            raise HTTPException(status_code=404, detail="remediation plan not found")
        return _serialize(plan)

    @router.get("/incidents/{incident_id}/audit")
    async def incident_audit(incident_id: UUID, session=Depends(get_session)):
        entries = await SqlAlchemyAuditLog(session).for_incident(IncidentId(incident_id))
        return [
            {"at": e.at.isoformat(), "plan_id": str(e.plan_id), "event": e.event, "detail": e.detail}
            for e in entries
        ]

    return router
