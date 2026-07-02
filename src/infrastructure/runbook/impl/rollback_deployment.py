from src.domain.runbook.contract import (
    ActionResult,
    DryRunResult,
    RiskTier,
    Runbook,
    RunbookId,
)
from src.infrastructure.kubernetes.cluster_client import (
    ClusterClient,
    DeploymentState,
    ReplicaSetRevision,
)
from src.infrastructure.runbook.impl.params import require


class RollbackDeploymentRunbook(Runbook):
    """Roll a Deployment back to its previous revision (MEDIUM risk).

    Mirrors `kubectl rollout undo`: reads the Deployment's owned ReplicaSets (each carries a
    `deployment.kubernetes.io/revision` annotation) and patches `spec.template` to match the
    second-most-recent revision's pod template — "the fix when an incident began right after a
    deploy" (Phase 2 catalog description).

    Being MEDIUM risk, this never qualifies for Phase 4 auto-execute (`RuleBasedPolicyEngine`
    only grants `ALLOW_AUTO` to LOW-risk runbooks) — it always waits for a human, matching its
    higher blast potential versus rolling-restart/scale. `undo` is marked non-reversible: this
    runbook has no way to safely reconstruct "redeploy the newer revision" from `params` alone,
    so it refuses honestly rather than guessing; a human recovers via a fresh plan if needed.
    """

    id = RunbookId("rollback-deployment")
    risk_tier = RiskTier.MEDIUM

    def __init__(self, client: ClusterClient) -> None:
        self._client = client

    async def _previous_revision(
        self, namespace: str, name: str
    ) -> tuple[DeploymentState | None, ReplicaSetRevision | None]:
        state = await self._client.read_deployment(namespace, name)
        if state is None:
            return None, None
        revisions = await self._client.list_replica_set_revisions(namespace, state.selector)
        ordered = sorted(revisions, key=lambda r: r.revision, reverse=True)
        return state, ordered[1] if len(ordered) >= 2 else None

    async def dry_run(self, params: dict) -> DryRunResult:
        namespace, name = require(params, "namespace"), require(params, "deployment")
        state, previous = await self._previous_revision(namespace, name)
        if state is None:
            return DryRunResult(
                predicted_effect=f"deployment {namespace}/{name} not found — cannot roll back",
                reversible=False,
            )
        if previous is None:
            return DryRunResult(
                predicted_effect=f"{namespace}/{name} has no earlier revision to roll back to",
                reversible=False,
            )
        # Server-side dry-run validates the patch without persisting it.
        await self._client.patch_deployment(
            namespace, name, {"spec": {"template": previous.template}}, dry_run=True
        )
        return DryRunResult(
            predicted_effect=f"roll back {namespace}/{name} to revision {previous.revision}",
            reversible=False,
        )

    async def execute(self, params: dict) -> ActionResult:
        namespace, name = require(params, "namespace"), require(params, "deployment")
        state, previous = await self._previous_revision(namespace, name)
        if state is None:
            return ActionResult(
                succeeded=False, detail=f"deployment {namespace}/{name} not found"
            )
        if previous is None:
            return ActionResult(
                succeeded=False,
                detail=f"{namespace}/{name} has no earlier revision to roll back to",
            )
        await self._client.patch_deployment(
            namespace, name, {"spec": {"template": previous.template}}, dry_run=False
        )
        return ActionResult(
            succeeded=True,
            detail=f"rolled back {namespace}/{name} to revision {previous.revision}",
        )

    async def undo(self, params: dict) -> ActionResult:
        return ActionResult(
            succeeded=False,
            detail=(
                "rollback has no automatic inverse — redeploy the newer revision manually "
                "or via a fresh remediation plan"
            ),
        )
