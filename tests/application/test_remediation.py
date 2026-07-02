import pytest

from src.application.remediation.use_cases import DryRunRemediation
from src.domain.runbook.contract import RunbookId
from src.domain.runbook.exceptions import RunbookNotFoundError
from src.infrastructure.actuator.k8s_actuator import KubernetesActuator
from src.infrastructure.runbook.static_registry import StaticRunbookRegistry
from tests.fakes import FakeClusterClient


def _use_case(*, enabled: bool = False) -> DryRunRemediation:
    registry = StaticRunbookRegistry(FakeClusterClient({("prod", "checkout"): 3}))
    return DryRunRemediation(KubernetesActuator(registry, execution_enabled=enabled))


@pytest.mark.asyncio
async def test_dry_run_remediation_returns_predicted_effect():
    result = await _use_case().execute(
        RunbookId("scale-deployment"),
        {"namespace": "prod", "deployment": "checkout", "replicas": "5"},
    )
    assert "3 -> 5" in result.predicted_effect


@pytest.mark.asyncio
async def test_dry_run_remediation_rejects_unknown_runbook():
    with pytest.raises(RunbookNotFoundError):
        await _use_case().execute(RunbookId("nope"), {})
