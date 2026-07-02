from datetime import datetime, timezone

from src.domain.actuator.port import Actuator
from src.domain.auth.exceptions import PermissionDeniedError
from src.domain.auth.identity import Identity
from src.domain.incident.entities import Incident
from src.domain.incident.exceptions import IncidentNotFoundError
from src.domain.incident.repository import IncidentRepository
from src.domain.incident.value_objects import IncidentId, IncidentStatus
from src.domain.remediation.audit import AuditEntry, AuditLog
from src.domain.remediation.circuit_breaker import CircuitBreaker
from src.domain.remediation.entities import RemediationPlan, RemediationStatus
from src.domain.remediation.recovery import RecoveryProbe
from src.domain.remediation.environment import Environment
from src.domain.remediation.exceptions import (
    ActuationDisabledError,
    CircuitOpenError,
    InvalidPlanTransitionError,
    RemediationPlanNotFoundError,
)
from src.domain.remediation.outcome import OutcomeStore
from src.domain.remediation.policy import PolicyEngine, PolicyInput, PolicyOutcome
from src.domain.remediation.repository import RemediationPlanRepository
from src.domain.remediation.selector import RemediationSelector
from src.domain.remediation.value_objects import RemediationPlanId
from src.domain.topology.blast_radius import blast_radius
from src.domain.topology.service_graph import ServiceGraph
from src.domain.runbook.contract import DryRunResult, RunbookId
from src.domain.runbook.exceptions import RunbookNotFoundError
from src.domain.runbook.spec import RunbookCatalog

PLAN_PERMISSION = "remediation:plan"


def _execute_permission(environment: Environment) -> str:
    return f"action:execute:{environment.value}"


def _status_for(outcome: PolicyOutcome) -> RemediationStatus:
    """Map a policy verdict to the plan's resulting status. ALLOW_AUTO is auto-approval — the
    plan is APPROVED with no human, ready for the auto executor."""
    if outcome is PolicyOutcome.DENY:
        return RemediationStatus.DENIED
    if outcome is PolicyOutcome.ALLOW_AUTO:
        return RemediationStatus.APPROVED
    return RemediationStatus.PENDING_APPROVAL


class DryRunRemediation:
    """Predict a runbook's effect against the cluster without mutating anything (Phase 3a).

    Read-only by construction: it only ever calls `Actuator.dry_run`, which is server-side and
    never persists. This is the seed of `PlanRemediation` — Phase 3b grows it into
    validate → policy-evaluate → dry-run → open-for-approval; for now it is just the dry-run
    step, so the whole actuation path can be exercised end-to-end while execution stays off.
    An unknown runbook id raises `RunbookNotFoundError` from the actuator.
    """

    def __init__(self, actuator: Actuator) -> None:
        self._actuator = actuator

    async def execute(self, runbook_id: RunbookId, params: dict) -> DryRunResult:
        return await self._actuator.dry_run(runbook_id, params)


class PlanRemediation:
    """Draft a remediation plan and run it through the guarded decision pipeline (Phase 3b).

    draft → dry-run (read-only, predicts the effect) → policy.evaluate → set status
    (PENDING_APPROVAL or DENIED), appending an immutable audit line at each step. **Nothing is
    executed** — this only decides whether the plan is *eligible* and, if so, leaves it awaiting
    human approval. A runbook that has a catalog spec but no executable implementation is
    rejected (`RunbookNotFoundError`) — you can only plan what you can actually run.
    """

    def __init__(
        self,
        actuator: Actuator,
        policy: PolicyEngine,
        catalog: RunbookCatalog,
        audit: AuditLog,
        plans: RemediationPlanRepository,
        incidents: IncidentRepository | None = None,
        outcomes: OutcomeStore | None = None,
        service_graph: ServiceGraph | None = None,
    ) -> None:
        self._actuator = actuator
        self._policy = policy
        self._catalog = catalog
        self._audit = audit
        self._plans = plans
        # Optional: without them the plan simply never qualifies for auto (success_rate stays 0,
        # blast radius unknown => 0). The route/loop wire the real collaborators.
        self._incidents = incidents
        self._outcomes = outcomes
        self._service_graph = service_graph

    async def execute(
        self,
        identity: Identity,
        incident_id: IncidentId,
        runbook_id: RunbookId,
        environment: Environment,
        params: dict,
        confidence: float = 1.0,
    ) -> RemediationPlan:
        if not identity.has(PLAN_PERMISSION):
            raise PermissionDeniedError(PLAN_PERMISSION)

        spec = self._catalog.get_spec(runbook_id)
        if spec is None:
            raise RunbookNotFoundError(runbook_id)

        plan = RemediationPlan.draft(
            incident_id,
            runbook_id,
            environment,
            spec.risk_tier,
            identity.subject,
            params,
            confidence,
        )
        await self._record(plan, "drafted", f"{runbook_id} in {environment.value}")

        # Dry-run is read-only; may raise RunbookNotFoundError if the runbook has no executable.
        dry_run = await self._actuator.dry_run(runbook_id, params)
        plan = plan.with_dry_run(dry_run)
        await self._record(plan, "dry_run", dry_run.predicted_effect)

        incident = (
            await self._incidents.get_by_id(incident_id) if self._incidents else None
        )
        rate, samples = await self._track_record(incident, runbook_id)
        blast = self._blast_radius(incident)
        if self._service_graph is not None and incident is not None:
            await self._record(
                plan,
                "blast_radius",
                f"{len(blast)} downstream service(s) affected"
                + (f": {', '.join(sorted(blast))}" if blast else ""),
            )
        decision = self._policy.evaluate(
            PolicyInput(
                runbook_id=runbook_id,
                environment=environment,
                risk_tier=spec.risk_tier,
                reversible=dry_run.reversible,
                confidence=confidence,
                success_rate=rate,
                sample_size=samples,
                blast_radius=len(blast),
            )
        )
        plan = plan.with_decision(decision).to_status(_status_for(decision.outcome))
        await self._record(
            plan, "policy_decision", f"{decision.outcome.value}: {'; '.join(decision.reasons)}"
        )
        if decision.outcome is PolicyOutcome.ALLOW_AUTO:
            # Policy stood in for the human — record that explicitly for the audit trail.
            await self._record(plan, "auto_approved", "; ".join(decision.reasons))
        await self._plans.save(plan)
        return plan

    async def _track_record(
        self, incident: Incident | None, runbook_id: RunbookId
    ) -> tuple[float, int]:
        if self._outcomes is None or incident is None:
            return 0.0, 0
        stats = await self._outcomes.stats(runbook_id, str(incident.fingerprint))
        return stats.success_rate, stats.sample_size

    def _blast_radius(self, incident: Incident | None) -> frozenset[str]:
        if self._service_graph is None or incident is None:
            return frozenset()
        return blast_radius(self._service_graph, incident.fingerprint.service)

    async def _record(self, plan: RemediationPlan, event: str, detail: str) -> None:
        await self._audit.append(
            AuditEntry(
                at=datetime.now(timezone.utc),
                incident_id=plan.incident_id,
                plan_id=plan.id,
                event=event,
                detail=detail,
            )
        )


class ApproveRemediation:
    """Human approval of a pending plan (Phase 3b) — the guarded gate before any execution.

    Checks the approver holds the permission the policy decision demands (env-scoped, e.g.
    `action:execute:staging`), then records the approval on the plan. The domain enforces the
    rest: the plan must be PENDING_APPROVAL and the approver cannot be the requester. **Approval
    does not execute anything** — in Phase 3b execution is still off; this only advances the plan
    to APPROVED with a full audit line naming who approved and with which permission.
    """

    def __init__(self, plans: RemediationPlanRepository, audit: AuditLog) -> None:
        self._plans = plans
        self._audit = audit

    async def execute(
        self, plan_id: RemediationPlanId, identity: Identity
    ) -> RemediationPlan:
        plan = await self._plans.get(plan_id)
        if plan is None:
            raise RemediationPlanNotFoundError(plan_id)

        required = plan.decision.required_permission if plan.decision else None
        if required and not identity.has(required):
            raise PermissionDeniedError(required)

        plan = plan.approve(identity.subject, required or "", datetime.now(timezone.utc))
        await self._plans.save(plan)
        await self._audit.append(
            AuditEntry(
                at=datetime.now(timezone.utc),
                incident_id=plan.incident_id,
                plan_id=plan.id,
                event="approved",
                detail=f"approved by {identity.subject} with '{required}'",
            )
        )
        return plan


class ExecuteRemediation:
    """Actually run an approved plan against the cluster — the first line that mutates (Phase 3c).

    Layers every guardrail before touching the actuator: the caller must hold the env-scoped
    execute permission; the plan must be APPROVED; the environment + runbook must be on the
    explicit enablement allowlist (the narrow Phase 3c opening, e.g. LOCAL + rolling-restart);
    and the circuit breaker must not have tripped for the incident. Only then does it call the
    actuator (which still enforces the global `ACTUATION_ENABLED` kill-switch). On failure it
    attempts `undo` when the action is reversible. Every step is audited, and the outcome feeds
    the flywheel (`OutcomeStore`). Auto-execute without a human is still out of scope — a person
    approved this plan first.
    """

    def __init__(
        self,
        actuator: Actuator,
        plans: RemediationPlanRepository,
        audit: AuditLog,
        breaker: CircuitBreaker,
        enabled_environments: frozenset[str],
        enabled_runbooks: frozenset[str],
    ) -> None:
        self._actuator = actuator
        self._plans = plans
        self._audit = audit
        self._breaker = breaker
        self._enabled_environments = enabled_environments
        self._enabled_runbooks = enabled_runbooks

    async def execute(
        self, plan_id: RemediationPlanId, identity: Identity
    ) -> RemediationPlan:
        plan = await self._plans.get(plan_id)
        if plan is None:
            raise RemediationPlanNotFoundError(plan_id)

        permission = _execute_permission(plan.environment)
        if not identity.has(permission):
            raise PermissionDeniedError(permission)
        if plan.status is not RemediationStatus.APPROVED:
            raise InvalidPlanTransitionError(
                f"cannot execute a plan in status '{plan.status.value}'"
            )

        if (
            plan.environment.value not in self._enabled_environments
            or str(plan.runbook_id) not in self._enabled_runbooks
        ):
            await self._record(plan, "execution_blocked", "environment/runbook not enabled")
            raise ActuationDisabledError(
                f"execution not enabled for {plan.runbook_id} in {plan.environment.value}"
            )

        if not await self._breaker.allow(plan.incident_id):
            await self._record(plan, "circuit_open", "too many actions — escalating to a human")
            raise CircuitOpenError(f"circuit open for incident {plan.incident_id}")

        plan = plan.to_status(RemediationStatus.EXECUTING)
        await self._plans.save(plan)
        await self._record(plan, "executing", f"{plan.runbook_id} via actuator")

        result = await self._actuator.execute(plan.runbook_id, plan.params)
        await self._breaker.record(plan.incident_id)

        # The *verified* outcome (did it fix the incident?) is recorded by VerifyRemediation —
        # execution succeeding is necessary but not sufficient for the flywheel to count a win.
        if result.succeeded:
            plan = plan.to_status(RemediationStatus.EXECUTED)
            await self._record(plan, "executed", result.detail)
        else:
            plan = await self._rollback(plan)
        await self._plans.save(plan)
        return plan

    async def _rollback(self, plan: RemediationPlan) -> RemediationPlan:
        await self._record(plan, "failed", "execution failed")
        if plan.dry_run and plan.dry_run.reversible:
            undo = await self._actuator.undo(plan.runbook_id, plan.params)
            await self._record(plan, "rolled_back", undo.detail)
            return plan.to_status(RemediationStatus.ROLLED_BACK)
        return plan.to_status(RemediationStatus.FAILED)

    async def _record(self, plan: RemediationPlan, event: str, detail: str) -> None:
        await self._audit.append(
            AuditEntry(
                at=datetime.now(timezone.utc),
                incident_id=plan.incident_id,
                plan_id=plan.id,
                event=event,
                detail=detail,
            )
        )


class AutoRemediationSweep:
    """Phase 4b: drive the closed loop with *no human even initiating*.

    One pass over the open incidents: for each that has no plan yet, deterministically select a
    runbook and hand it to `AutoRemediate`. Idempotent — an incident is planned at most once, so
    the loop won't spam re-plans; if trust isn't earned the plan simply waits for a human, and a
    future pass skips it. Returns the incident ids acted on (planned or auto-healed).
    """

    def __init__(
        self,
        incidents: IncidentRepository,
        plans: RemediationPlanRepository,
        selector: RemediationSelector,
        auto_remediate: "AutoRemediate",
    ) -> None:
        self._incidents = incidents
        self._plans = plans
        self._selector = selector
        self._auto_remediate = auto_remediate

    async def run(self) -> list[IncidentId]:
        acted: list[IncidentId] = []
        for incident in await self._incidents.list_open():
            if await self._plans.exists_for_incident(incident.id):
                continue
            candidate = self._selector.select(incident)
            if candidate is None:
                continue
            await self._auto_remediate.execute(
                incident.id,
                candidate.runbook_id,
                candidate.environment,
                candidate.params,
            )
            acted.append(incident.id)
        return acted


class AutoRemediate:
    """Closed-loop autonomy (Phase 4a): plan → if policy *earned* auto, execute + verify with no
    human; otherwise leave the pending plan for a human.

    The autonomy is authorised by the policy's `ALLOW_AUTO` decision (which only fires once a
    runbook's verified success rate for this fingerprint clears the ratchet), not by any person —
    so Aegis acts as a system principal (`aegis-auto`). Every Phase 3 guardrail still runs
    underneath: dry-run, enablement allowlist, circuit breaker, kill-switch, audit, and the
    verify that follows. If trust hasn't been earned the plan comes back PENDING_APPROVAL and a
    human decides, exactly as in Phase 3.
    """

    SYSTEM_SUBJECT = "aegis-auto"

    def __init__(
        self,
        planner: "PlanRemediation",
        executor: "ExecuteRemediation",
        verifier: "VerifyRemediation",
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._verifier = verifier

    async def execute(
        self,
        incident_id: IncidentId,
        runbook_id: RunbookId,
        environment: Environment,
        params: dict,
        confidence: float = 1.0,
    ) -> RemediationPlan:
        system = Identity(
            self.SYSTEM_SUBJECT,
            frozenset({PLAN_PERMISSION, _execute_permission(environment)}),
        )
        plan = await self._planner.execute(
            system, incident_id, runbook_id, environment, params, confidence
        )
        if plan.decision is None or plan.decision.outcome is not PolicyOutcome.ALLOW_AUTO:
            return plan  # trust not earned — a human approves this one

        executed = await self._executor.execute(plan.id, system)
        if executed.status is RemediationStatus.EXECUTED:
            await self._verifier.execute(executed.id)
        return executed


class VerifyRemediation:
    """Close the loop (ARCHITECTURE.md §3.6): after an execution, did the incident recover?

    Probes the triggering condition over a recovery window. Recovered → resolve the incident and
    record a **success** to the flywheel. Not recovered → escalate the incident to a human and
    record a **failure**. This is the step that makes Aegis closed-loop rather than
    fire-and-forget, and the recorded outcome here — not the mere fact that execution ran — is
    what a runbook must accumulate to earn Phase 4 auto-execute. Read-only w.r.t. the cluster.
    """

    def __init__(
        self,
        plans: RemediationPlanRepository,
        incidents: IncidentRepository,
        audit: AuditLog,
        outcomes: OutcomeStore,
        probe: RecoveryProbe,
    ) -> None:
        self._plans = plans
        self._incidents = incidents
        self._audit = audit
        self._outcomes = outcomes
        self._probe = probe

    async def execute(self, plan_id: RemediationPlanId) -> Incident:
        plan = await self._plans.get(plan_id)
        if plan is None:
            raise RemediationPlanNotFoundError(plan_id)
        if plan.status is not RemediationStatus.EXECUTED:
            raise InvalidPlanTransitionError(
                f"cannot verify a plan in status '{plan.status.value}' (not executed)"
            )
        incident = await self._incidents.get_by_id(plan.incident_id)
        if incident is None:
            raise IncidentNotFoundError(str(plan.incident_id))

        recovered = await self._probe.is_recovered(incident)
        await self._outcomes.record(
            plan.runbook_id, str(incident.fingerprint), recovered
        )

        if recovered:
            incident = incident.resolve()
            await self._record(plan, "verified", "incident recovered — resolved")
        else:
            incident = incident.transition_to(IncidentStatus.ESCALATED)
            await self._record(
                plan, "verify_failed", "not recovered — escalated to a human"
            )
        await self._incidents.update(incident)
        return incident

    async def _record(self, plan: RemediationPlan, event: str, detail: str) -> None:
        await self._audit.append(
            AuditEntry(
                at=datetime.now(timezone.utc),
                incident_id=plan.incident_id,
                plan_id=plan.id,
                event=event,
                detail=detail,
            )
        )
