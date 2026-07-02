"""One-shot dry-run driver (Phase 3a).

Predicts a runbook's effect against a live cluster via server-side dry-run. **Never mutates
anything** — even with ACTUATION_ENABLED=true this only calls the actuator's dry-run path.

    KUBE_CONTEXT=minikube uv run python -m src.dry_run rolling-restart namespace=default deployment=web
    KUBE_CONTEXT=minikube uv run python -m src.dry_run scale-deployment namespace=default deployment=web replicas=3
"""

import asyncio
import sys

from src.application.remediation.use_cases import DryRunRemediation
from src.domain.runbook.contract import RunbookId
from src.domain.runbook.exceptions import RunbookNotFoundError
from src.infrastructure.actuator.k8s_actuator import KubernetesActuator
from src.infrastructure.config import get_settings
from src.infrastructure.kubernetes.cluster_client import KubernetesClusterClient
from src.infrastructure.runbook.static_registry import StaticRunbookRegistry


async def main() -> None:
    settings = get_settings()
    if not settings.kube_context:
        raise SystemExit("Set KUBE_CONTEXT (e.g. minikube) to dry-run against a cluster.")

    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("usage: python -m src.dry_run <runbook-id> key=value ...")
    runbook_id = RunbookId(argv[0])
    params = dict(pair.split("=", 1) for pair in argv[1:] if "=" in pair)

    client = KubernetesClusterClient(settings.kube_context)
    registry = StaticRunbookRegistry(client)
    # Dry-run is read-only regardless of the kill-switch; pass the real setting for honesty.
    actuator = KubernetesActuator(
        registry, execution_enabled=settings.actuation_enabled
    )

    try:
        result = await DryRunRemediation(actuator).execute(runbook_id, params)
    except RunbookNotFoundError as error:
        raise SystemExit(str(error)) from error

    print(f"runbook:          {runbook_id}")
    print(f"params:           {params}")
    print(f"reversible:       {result.reversible}")
    print(f"predicted effect: {result.predicted_effect}")


if __name__ == "__main__":
    asyncio.run(main())
