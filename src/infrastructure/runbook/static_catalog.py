from src.domain.runbook.contract import RiskTier, RunbookId
from src.domain.runbook.spec import RunbookCatalog, RunbookSpec

# Phase 2 seed catalog — the menu of remediation the LLM may reference. These are specs
# only; executable implementations arrive in Phase 3 behind the policy/guardrail machinery.
SEED_SPECS: tuple[RunbookSpec, ...] = (
    RunbookSpec(
        id=RunbookId("rolling-restart"),
        description="Rolling-restart a deployment's pods to reclaim resources or clear a "
        "transient bad state, with no downtime (surge one pod at a time).",
        risk_tier=RiskTier.LOW,
        parameters=("namespace", "deployment"),
    ),
    RunbookSpec(
        id=RunbookId("scale-deployment"),
        description="Scale a deployment's replica count up or down to absorb load or relieve "
        "resource pressure.",
        risk_tier=RiskTier.LOW,
        parameters=("namespace", "deployment", "replicas"),
    ),
    RunbookSpec(
        id=RunbookId("rollback-deployment"),
        description="Roll a deployment back to its previous revision — the fix when an "
        "incident began right after a deploy.",
        risk_tier=RiskTier.MEDIUM,
        parameters=("namespace", "deployment"),
    ),
    RunbookSpec(
        id=RunbookId("increase-memory-limit"),
        description="Raise a container's memory limit/request to stop repeated OOMKills while "
        "a leak is investigated.",
        risk_tier=RiskTier.LOW,
        parameters=("namespace", "deployment", "memory_limit"),
    ),
    RunbookSpec(
        id=RunbookId("cordon-drain-node"),
        description="Cordon and drain a misbehaving node, rescheduling its pods elsewhere.",
        risk_tier=RiskTier.HIGH,
        parameters=("node",),
    ),
)


class StaticRunbookCatalog(RunbookCatalog):
    def __init__(self, specs: tuple[RunbookSpec, ...] = SEED_SPECS) -> None:
        self._by_id = {spec.id: spec for spec in specs}

    def list_specs(self) -> list[RunbookSpec]:
        return list(self._by_id.values())

    def get_spec(self, runbook_id: RunbookId) -> RunbookSpec | None:
        return self._by_id.get(runbook_id)
