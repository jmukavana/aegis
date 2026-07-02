import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeploymentState:
    """The slice of a Deployment's state runbooks reason about (Phase 3a).

    `selector` is the Deployment's pod-label matcher (`spec.selector.matchLabels`) — needed to
    find the ReplicaSets it owns (Phase 4c rollback), since those aren't addressed by name.
    """

    namespace: str
    name: str
    replicas: int
    selector: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplicaSetRevision:
    """One revision in a Deployment's rollout history (Phase 4c rollback-deployment).

    `template` is the ReplicaSet's pod template, already JSON-serializable (camelCase) — patch
    a Deployment's `spec.template` with a previous revision's to roll back, the same mechanism
    `kubectl rollout undo` uses.
    """

    revision: int
    template: dict


class ClusterClient(ABC):
    """Narrow port over the exact Kubernetes operations runbooks need.

    This is the seam that keeps the k8s client import in *one* adapter: runbooks build patches
    as plain dicts and hand them here, so runbook logic is pure and unit-testable with a fake.
    `patch_deployment(dry_run=True)` uses server-side dry-run (`dryRun=All`) — the API server
    itself validates and predicts the result, so a dry-run is genuinely read-only.
    """

    @abstractmethod
    async def read_deployment(self, namespace: str, name: str) -> DeploymentState | None: ...

    @abstractmethod
    async def patch_deployment(
        self, namespace: str, name: str, patch: dict, *, dry_run: bool
    ) -> DeploymentState: ...

    @abstractmethod
    async def list_replica_set_revisions(
        self, namespace: str, selector: dict[str, str]
    ) -> list[ReplicaSetRevision]:
        """The ReplicaSets a Deployment owns (matched by `selector`), one per rollout revision."""


class KubernetesClusterClient(ClusterClient):  # pragma: no cover - needs a cluster
    """Real adapter over the official `kubernetes` client (AppsV1).

    Synchronous under the hood (the k8s client is blocking), so every call is offloaded to a
    worker thread with `asyncio.to_thread` to stay non-blocking — the same sync↔async bridge
    the Phase 1 event watcher uses. The API/AppsV1 client is built lazily on first use so the
    app and tests boot with no cluster.
    """

    def __init__(self, kube_context: str = "") -> None:
        self._kube_context = kube_context
        self._apps: object | None = None

    def _api(self):
        from kubernetes import client, config

        if self._apps is None:
            if self._kube_context:
                config.load_kube_config(context=self._kube_context)
            else:
                config.load_incluster_config()
            self._apps = client.AppsV1Api()
        return self._apps

    async def read_deployment(self, namespace: str, name: str) -> DeploymentState | None:
        return await asyncio.to_thread(self._read_deployment, namespace, name)

    def _read_deployment(self, namespace: str, name: str) -> DeploymentState | None:
        from kubernetes.client.exceptions import ApiException

        try:
            dep = self._api().read_namespaced_deployment(name=name, namespace=namespace)
        except ApiException as error:
            if error.status == 404:
                return None
            raise
        selector = dict((dep.spec.selector.match_labels or {}) if dep.spec.selector else {})
        return DeploymentState(namespace, name, dep.spec.replicas or 0, selector)

    async def patch_deployment(
        self, namespace: str, name: str, patch: dict, *, dry_run: bool
    ) -> DeploymentState:
        return await asyncio.to_thread(
            self._patch_deployment, namespace, name, patch, dry_run
        )

    def _patch_deployment(
        self, namespace: str, name: str, patch: dict, dry_run: bool
    ) -> DeploymentState:
        dep = self._api().patch_namespaced_deployment(
            name=name,
            namespace=namespace,
            body=patch,
            # dryRun=All => the API server validates + predicts without persisting anything.
            dry_run="All" if dry_run else None,
            field_manager="aegis",
        )
        selector = dict((dep.spec.selector.match_labels or {}) if dep.spec.selector else {})
        return DeploymentState(namespace, name, dep.spec.replicas or 0, selector)

    async def list_replica_set_revisions(
        self, namespace: str, selector: dict[str, str]
    ) -> list[ReplicaSetRevision]:
        return await asyncio.to_thread(
            self._list_replica_set_revisions, namespace, selector
        )

    def _list_replica_set_revisions(
        self, namespace: str, selector: dict[str, str]
    ) -> list[ReplicaSetRevision]:
        from kubernetes import client

        label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
        result = self._api().list_namespaced_replica_set(
            namespace, label_selector=label_selector
        )
        api_client = client.ApiClient()
        revisions = []
        for rs in result.items:
            annotations = rs.metadata.annotations or {}
            revision = int(annotations.get("deployment.kubernetes.io/revision", "0"))
            # sanitize_for_serialization -> proper camelCase JSON, valid as a patch body.
            template = api_client.sanitize_for_serialization(rs.spec.template)
            revisions.append(ReplicaSetRevision(revision=revision, template=template))
        return sorted(revisions, key=lambda r: r.revision, reverse=True)
