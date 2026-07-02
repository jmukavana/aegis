from src.domain.runbook.contract import (
    ActionResult,
    DryRunResult,
    RiskTier,
    Runbook,
    RunbookId,
)
from src.infrastructure.kubernetes.cluster_client import ClusterClient
from src.infrastructure.runbook.impl.params import require, require_int


class ScaleDeploymentRunbook(Runbook):
    """Scale a Deployment's replica count (LOW risk, reversible, idempotent).

    Params: `namespace`, `deployment`, `replicas`. `undo` scales back to `previous_replicas`
    (captured and reported by `execute`) — so a failed remediation can be rolled back.
    """

    id = RunbookId("scale-deployment")
    risk_tier = RiskTier.LOW

    def __init__(self, client: ClusterClient) -> None:
        self._client = client

    async def dry_run(self, params: dict) -> DryRunResult:
        namespace, name = require(params, "namespace"), require(params, "deployment")
        replicas = require_int(params, "replicas")
        state = await self._client.read_deployment(namespace, name)
        if state is None:
            return DryRunResult(
                predicted_effect=f"deployment {namespace}/{name} not found — cannot scale",
                reversible=False,
            )
        await self._client.patch_deployment(
            namespace, name, {"spec": {"replicas": replicas}}, dry_run=True
        )
        return DryRunResult(
            predicted_effect=(
                f"scale {namespace}/{name}: replicas {state.replicas} -> {replicas}"
            ),
            reversible=True,
        )

    async def execute(self, params: dict) -> ActionResult:
        namespace, name = require(params, "namespace"), require(params, "deployment")
        replicas = require_int(params, "replicas")
        state = await self._client.read_deployment(namespace, name)
        if state is None:
            return ActionResult(
                succeeded=False, detail=f"deployment {namespace}/{name} not found"
            )
        await self._client.patch_deployment(
            namespace, name, {"spec": {"replicas": replicas}}, dry_run=False
        )
        # Report the prior count so the caller can populate `previous_replicas` for undo.
        return ActionResult(
            succeeded=True,
            detail=(
                f"scaled {namespace}/{name} to {replicas} replicas (was {state.replicas}; "
                f"undo with previous_replicas={state.replicas})"
            ),
        )

    async def undo(self, params: dict) -> ActionResult:
        namespace, name = require(params, "namespace"), require(params, "deployment")
        previous = require_int(params, "previous_replicas")
        await self._client.patch_deployment(
            namespace, name, {"spec": {"replicas": previous}}, dry_run=False
        )
        return ActionResult(
            succeeded=True,
            detail=f"restored {namespace}/{name} to {previous} replicas",
        )
