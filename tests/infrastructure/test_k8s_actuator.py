import pytest

from src.domain.runbook.contract import RunbookId
from src.domain.runbook.exceptions import RunbookNotFoundError
from src.infrastructure.actuator.k8s_actuator import KubernetesActuator
from src.infrastructure.runbook.static_registry import StaticRunbookRegistry
from tests.fakes import FakeClusterClient


def _actuator(*, enabled: bool) -> KubernetesActuator:
    registry = StaticRunbookRegistry(FakeClusterClient({("prod", "checkout"): 3}))
    return KubernetesActuator(registry, execution_enabled=enabled)


@pytest.mark.asyncio
async def test_dry_run_is_allowed_even_when_execution_disabled():
    result = await _actuator(enabled=False).dry_run(
        RunbookId("rolling-restart"), {"namespace": "prod", "deployment": "checkout"}
    )
    assert "rolling-restart prod/checkout" in result.predicted_effect


@pytest.mark.asyncio
async def test_execute_refuses_while_kill_switch_off():
    result = await _actuator(enabled=False).execute(
        RunbookId("rolling-restart"), {"namespace": "prod", "deployment": "checkout"}
    )
    assert result.succeeded is False
    assert "disabled" in result.detail.lower()


@pytest.mark.asyncio
async def test_execute_runs_when_enabled():
    result = await _actuator(enabled=True).execute(
        RunbookId("rolling-restart"), {"namespace": "prod", "deployment": "checkout"}
    )
    assert result.succeeded is True


@pytest.mark.asyncio
async def test_unknown_runbook_is_rejected():
    with pytest.raises(RunbookNotFoundError):
        await _actuator(enabled=True).dry_run(RunbookId("delete-prod-database"), {})


@pytest.mark.asyncio
async def test_registry_resolves_all_three_phase_3_4_executables():
    registry = StaticRunbookRegistry(FakeClusterClient({("prod", "checkout"): 3}))
    ids = {str(r.id) for r in registry.list_all()}
    assert ids == {"rolling-restart", "scale-deployment", "rollback-deployment"}
