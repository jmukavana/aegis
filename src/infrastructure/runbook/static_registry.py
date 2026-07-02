from src.domain.runbook.contract import Runbook, RunbookId, RunbookRegistry
from src.infrastructure.kubernetes.cluster_client import ClusterClient
from src.infrastructure.runbook.impl.rollback_deployment import RollbackDeploymentRunbook
from src.infrastructure.runbook.impl.rolling_restart import RollingRestartRunbook
from src.infrastructure.runbook.impl.scale_deployment import ScaleDeploymentRunbook


class StaticRunbookRegistry(RunbookRegistry):
    """The executable runbooks Aegis can actuate (Phase 3a, extended Phase 4c).

    Deliberately a subset of the Phase 2 *catalog* (`StaticRunbookCatalog`): the catalog is the
    menu the LLM may reference, this registry is the kitchen. Only runbooks with real, reviewed
    implementations live here — the two lowest-risk, reversible, idempotent ones
    (rolling-restart, scale-deployment) plus rollback-deployment (MEDIUM risk — it can be
    human-approved and executed like the others, but never qualifies for Phase 4 auto-execute,
    which is LOW-risk only). Being registered here does not itself enable execution — the
    per-environment/per-runbook `ACTUATION_RUNBOOKS` allowlist (Phase 3c) still gates that.
    """

    def __init__(self, client: ClusterClient) -> None:
        runbooks: tuple[Runbook, ...] = (
            RollingRestartRunbook(client),
            ScaleDeploymentRunbook(client),
            RollbackDeploymentRunbook(client),
        )
        self._by_id = {runbook.id: runbook for runbook in runbooks}

    def get(self, runbook_id: RunbookId) -> Runbook | None:
        return self._by_id.get(runbook_id)

    def list_all(self) -> list[Runbook]:
        return list(self._by_id.values())
