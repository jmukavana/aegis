from datetime import datetime, timezone

from src.domain.runbook.contract import (
    ActionResult,
    DryRunResult,
    RiskTier,
    Runbook,
    RunbookId,
)
from src.infrastructure.kubernetes.cluster_client import ClusterClient
from src.infrastructure.runbook.impl.params import require

# Patching this pod-template annotation is how `kubectl rollout restart` triggers a rolling
# restart: it changes the template hash, so the Deployment recreates pods one surge at a time.
RESTART_ANNOTATION = "aegis.dev/restartedAt"


class RollingRestartRunbook(Runbook):
    """Rolling-restart a Deployment's pods (LOW risk, reversible, idempotent, no downtime).

    Params: `namespace`, `deployment`. Reclaims memory / clears a transient bad state without
    dropping traffic. There is no meaningful inverse — a restart is non-destructive — so `undo`
    is a successful no-op.
    """

    id = RunbookId("rolling-restart")
    risk_tier = RiskTier.LOW

    def __init__(self, client: ClusterClient) -> None:
        self._client = client

    def _patch(self) -> dict:
        stamp = datetime.now(timezone.utc).isoformat()
        return {
            "spec": {
                "template": {"metadata": {"annotations": {RESTART_ANNOTATION: stamp}}}
            }
        }

    async def dry_run(self, params: dict) -> DryRunResult:
        namespace, name = require(params, "namespace"), require(params, "deployment")
        state = await self._client.read_deployment(namespace, name)
        if state is None:
            return DryRunResult(
                predicted_effect=f"deployment {namespace}/{name} not found — nothing to restart",
                reversible=True,
            )
        # Server-side dry-run validates the patch without persisting it.
        await self._client.patch_deployment(namespace, name, self._patch(), dry_run=True)
        return DryRunResult(
            predicted_effect=(
                f"rolling-restart {namespace}/{name}: recreate {state.replicas} pod(s) "
                "one surge at a time, no downtime"
            ),
            reversible=True,
        )

    async def execute(self, params: dict) -> ActionResult:
        namespace, name = require(params, "namespace"), require(params, "deployment")
        await self._client.patch_deployment(namespace, name, self._patch(), dry_run=False)
        return ActionResult(
            succeeded=True, detail=f"rolling-restart triggered for {namespace}/{name}"
        )

    async def undo(self, params: dict) -> ActionResult:
        return ActionResult(
            succeeded=True,
            detail="rolling-restart has no inverse; it is non-destructive, nothing to undo",
        )
