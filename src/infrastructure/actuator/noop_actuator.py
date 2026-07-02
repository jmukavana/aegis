from src.domain.actuator.port import Actuator
from src.domain.runbook.contract import ActionResult, DryRunResult, RunbookId


class NoopActuator(Actuator):
    """Phase 0 actuator adapter: does nothing but report what it *would* do.

    This is the placeholder behind the `Actuator` port (ARCHITECTURE.md ADR-0001). Phase 3
    replaces it with a real Kubernetes adapter (official `kubernetes` client), and — if ever
    needed — a Go implementation can take its place behind this same port. Executing is
    deliberately refused here so nothing can mutate a cluster before the guardrail/policy
    machinery exists.
    """

    async def dry_run(self, runbook_id: RunbookId, params: dict) -> DryRunResult:
        return DryRunResult(
            predicted_effect=f"[noop] would run '{runbook_id}' with {params}",
            reversible=True,
        )

    async def execute(self, runbook_id: RunbookId, params: dict) -> ActionResult:
        return ActionResult(
            succeeded=False,
            detail="Actuation is disabled in Phase 0 (no guardrails/policy yet).",
        )

    async def undo(self, runbook_id: RunbookId, params: dict) -> ActionResult:
        return ActionResult(succeeded=False, detail="Undo disabled in Phase 0.")
