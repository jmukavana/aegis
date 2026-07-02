from src.domain.remediation.value_objects import RemediationPlanId


class RemediationPlanNotFoundError(Exception):
    def __init__(self, plan_id: RemediationPlanId) -> None:
        super().__init__(f"No remediation plan with id '{plan_id}'")
        self.plan_id = plan_id


class InvalidPlanTransitionError(Exception):
    """Raised on an illegal status transition (e.g. approving a plan that isn't pending)."""


class SelfApprovalError(Exception):
    """Raised when the identity approving a plan is the one that drafted it.

    Segregation of duty (ARCHITECTURE.md §3.7): the requester cannot approve their own action.
    """


class ActuationDisabledError(Exception):
    """Raised when execution is attempted for an environment/runbook not on the enabled
    allowlist — the narrow, explicit opening of Phase 3c (e.g. LOCAL + rolling-restart only)."""


class CircuitOpenError(Exception):
    """Raised when the circuit breaker has tripped for an incident — too many actions in the
    window; the caller escalates to a human instead of acting again."""
