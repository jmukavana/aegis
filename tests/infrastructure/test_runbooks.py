import pytest

from src.infrastructure.runbook.impl.params import InvalidRunbookParametersError
from src.infrastructure.runbook.impl.rolling_restart import (
    RESTART_ANNOTATION,
    RollingRestartRunbook,
)
from src.infrastructure.runbook.impl.scale_deployment import ScaleDeploymentRunbook
from tests.fakes import FakeClusterClient


# --- rolling-restart -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rolling_restart_dry_run_predicts_and_never_mutates():
    client = FakeClusterClient({("prod", "checkout"): 3})
    runbook = RollingRestartRunbook(client)

    result = await runbook.dry_run({"namespace": "prod", "deployment": "checkout"})

    assert "3 pod(s)" in result.predicted_effect
    assert result.reversible is True
    # Exactly one server-side dry-run patch carrying the restart annotation; no real patch.
    (ns, name, patch, dry_run), = client.patches
    assert (ns, name, dry_run) == ("prod", "checkout", True)
    annotations = patch["spec"]["template"]["metadata"]["annotations"]
    assert RESTART_ANNOTATION in annotations


@pytest.mark.asyncio
async def test_rolling_restart_dry_run_on_missing_deployment():
    client = FakeClusterClient()
    result = await RollingRestartRunbook(client).dry_run(
        {"namespace": "prod", "deployment": "ghost"}
    )
    assert "not found" in result.predicted_effect
    assert client.patches == []  # nothing attempted against a non-existent deployment


@pytest.mark.asyncio
async def test_rolling_restart_execute_issues_real_patch():
    client = FakeClusterClient({("prod", "checkout"): 3})
    result = await RollingRestartRunbook(client).execute(
        {"namespace": "prod", "deployment": "checkout"}
    )
    assert result.succeeded is True
    assert client.patches[-1][3] is False  # dry_run=False => a real patch


@pytest.mark.asyncio
async def test_rolling_restart_undo_is_noop_success():
    result = await RollingRestartRunbook(FakeClusterClient()).undo({})
    assert result.succeeded is True
    assert "no inverse" in result.detail


# --- scale-deployment ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scale_dry_run_shows_delta_without_mutating():
    client = FakeClusterClient({("prod", "checkout"): 2})
    result = await ScaleDeploymentRunbook(client).dry_run(
        {"namespace": "prod", "deployment": "checkout", "replicas": "5"}
    )
    assert "2 -> 5" in result.predicted_effect
    assert client.patches[-1][3] is True  # dry-run only
    assert (await client.read_deployment("prod", "checkout")).replicas == 2


@pytest.mark.asyncio
async def test_scale_execute_changes_replicas_and_reports_previous():
    client = FakeClusterClient({("prod", "checkout"): 2})
    result = await ScaleDeploymentRunbook(client).execute(
        {"namespace": "prod", "deployment": "checkout", "replicas": "5"}
    )
    assert result.succeeded is True
    assert "was 2" in result.detail
    assert (await client.read_deployment("prod", "checkout")).replicas == 5


@pytest.mark.asyncio
async def test_scale_undo_restores_previous_replicas():
    client = FakeClusterClient({("prod", "checkout"): 5})
    await ScaleDeploymentRunbook(client).undo(
        {"namespace": "prod", "deployment": "checkout", "previous_replicas": "2"}
    )
    assert (await client.read_deployment("prod", "checkout")).replicas == 2


@pytest.mark.asyncio
async def test_runbook_rejects_missing_parameters():
    with pytest.raises(InvalidRunbookParametersError):
        await RollingRestartRunbook(FakeClusterClient()).dry_run({"namespace": "prod"})
