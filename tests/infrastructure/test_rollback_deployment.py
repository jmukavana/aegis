import pytest

from src.infrastructure.kubernetes.cluster_client import ReplicaSetRevision
from src.infrastructure.runbook.impl.rollback_deployment import (
    RollbackDeploymentRunbook,
)
from tests.fakes import FakeClusterClient

PARAMS = {"namespace": "default", "deployment": "web"}


def _client_with_history(revisions: list[ReplicaSetRevision]) -> FakeClusterClient:
    return FakeClusterClient(
        deployments={("default", "web"): 3},
        replica_sets={("default", "web"): revisions},
    )


@pytest.mark.asyncio
async def test_dry_run_predicts_rollback_to_previous_revision_without_mutating():
    client = _client_with_history(
        [
            ReplicaSetRevision(revision=2, template={"metadata": {"labels": {"v": "2"}}}),
            ReplicaSetRevision(revision=1, template={"metadata": {"labels": {"v": "1"}}}),
        ]
    )
    result = await RollbackDeploymentRunbook(client).dry_run(PARAMS)

    assert "revision 1" in result.predicted_effect
    assert result.reversible is False
    # Only a server-side (dry-run) patch was attempted, never a real one.
    assert all(patch[3] is True for patch in client.patches)


@pytest.mark.asyncio
async def test_dry_run_reports_no_prior_revision_when_only_one_exists():
    client = _client_with_history(
        [ReplicaSetRevision(revision=1, template={})]
    )
    result = await RollbackDeploymentRunbook(client).dry_run(PARAMS)

    assert "no earlier revision" in result.predicted_effect
    assert client.patches == []  # nothing attempted — there's nothing to roll back to


@pytest.mark.asyncio
async def test_dry_run_on_missing_deployment():
    client = FakeClusterClient()
    result = await RollbackDeploymentRunbook(client).dry_run(PARAMS)
    assert "not found" in result.predicted_effect


@pytest.mark.asyncio
async def test_execute_patches_template_to_previous_revision():
    client = _client_with_history(
        [
            ReplicaSetRevision(revision=3, template={"metadata": {"labels": {"v": "3"}}}),
            ReplicaSetRevision(revision=2, template={"metadata": {"labels": {"v": "2"}}}),
            ReplicaSetRevision(revision=1, template={"metadata": {"labels": {"v": "1"}}}),
        ]
    )
    result = await RollbackDeploymentRunbook(client).execute(PARAMS)

    assert result.succeeded is True
    assert "revision 2" in result.detail
    namespace, name, patch, dry_run = client.patches[-1]
    assert dry_run is False
    assert patch["spec"]["template"]["metadata"]["labels"]["v"] == "2"


@pytest.mark.asyncio
async def test_execute_fails_gracefully_with_no_prior_revision():
    client = _client_with_history([ReplicaSetRevision(revision=1, template={})])
    result = await RollbackDeploymentRunbook(client).execute(PARAMS)
    assert result.succeeded is False
    assert "no earlier revision" in result.detail


@pytest.mark.asyncio
async def test_undo_honestly_refuses_rather_than_guessing():
    result = await RollbackDeploymentRunbook(FakeClusterClient()).undo(PARAMS)
    assert result.succeeded is False
    assert "no automatic inverse" in result.detail
