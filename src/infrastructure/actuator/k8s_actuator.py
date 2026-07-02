from src.domain.actuator.port import Actuator
from src.domain.runbook.contract import ActionResult, DryRunResult, RunbookId, RunbookRegistry
from src.domain.runbook.exceptions import RunbookNotFoundError

_DISABLED = (
    "Actuation is disabled (ACTUATION_ENABLED=false). Phase 3a is dry-run only; "
    "execution arrives in Phase 3c behind policy + human approval."
)


class KubernetesActuator(Actuator):
    """The guarded seam between the brain and the cluster (ARCHITECTURE.md ADR-0001).

    Not itself a Kubernetes client — it resolves a `runbook_id` to an executable `Runbook` via
    the registry and delegates, adding the cross-cutting guard: `dry_run` is always allowed
    (read-only, server-side), but `execute`/`undo` REFUSE unless execution is explicitly
    enabled (the global kill-switch, default off). An unknown runbook id is rejected outright.
    Per-environment / per-runbook enablement and the policy gate layer on top of this in
    Phase 3b/3c; this class owns only the kill-switch.
    """

    def __init__(self, registry: RunbookRegistry, *, execution_enabled: bool) -> None:
        self._registry = registry
        self._execution_enabled = execution_enabled

    def _runbook(self, runbook_id: RunbookId):
        runbook = self._registry.get(runbook_id)
        if runbook is None:
            raise RunbookNotFoundError(runbook_id)
        return runbook

    async def dry_run(self, runbook_id: RunbookId, params: dict) -> DryRunResult:
        return await self._runbook(runbook_id).dry_run(params)

    async def execute(self, runbook_id: RunbookId, params: dict) -> ActionResult:
        runbook = self._runbook(runbook_id)
        if not self._execution_enabled:
            return ActionResult(succeeded=False, detail=_DISABLED)
        return await runbook.execute(params)

    async def undo(self, runbook_id: RunbookId, params: dict) -> ActionResult:
        runbook = self._runbook(runbook_id)
        if not self._execution_enabled:
            return ActionResult(succeeded=False, detail=_DISABLED)
        return await runbook.undo(params)
