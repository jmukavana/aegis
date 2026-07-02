from datetime import datetime, timedelta

from src.domain.analysis.memory import IncidentMemory, RecalledIncident
from src.domain.analysis.models import IncidentAnalysis
from src.domain.change.change_log import ChangeEvent, ChangeLog
from src.domain.detection.metrics_gateway import MetricsGateway
from src.domain.incident.entities import Incident
from src.domain.incident.repository import IncidentRepository
from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.entities import RemediationPlan
from src.domain.remediation.recovery import RecoveryProbe
from src.domain.remediation.repository import RemediationPlanRepository
from src.domain.remediation.value_objects import RemediationPlanId
from src.domain.topology.service_graph import ServiceGraph
from src.infrastructure.kubernetes.cluster_client import (
    ClusterClient,
    DeploymentState,
    ReplicaSetRevision,
)


class InMemoryIncidentRepository(IncidentRepository):
    def __init__(self) -> None:
        self._incidents: list[Incident] = []

    async def add(self, incident: Incident) -> None:
        self._incidents.append(incident)

    async def get_by_id(self, incident_id: IncidentId) -> Incident | None:
        return next((i for i in self._incidents if i.id == incident_id), None)

    async def update(self, incident: Incident) -> None:
        self._incidents = [
            incident if i.id == incident.id else i for i in self._incidents
        ]

    async def list_all(self) -> list[Incident]:
        return list(self._incidents)

    async def list_open(self) -> list[Incident]:
        return [incident for incident in self._incidents if incident.is_open]


class InMemoryIncidentMemory(IncidentMemory):
    def __init__(self, recalls: list[RecalledIncident] | None = None) -> None:
        self._recalls = recalls or []
        self.remembered: list[tuple[Incident, IncidentAnalysis]] = []

    async def remember(self, incident: Incident, analysis: IncidentAnalysis) -> None:
        self.remembered.append((incident, analysis))

    async def recall(self, incident: Incident, limit: int) -> list[RecalledIncident]:
        return self._recalls[:limit]


class FakeRecoveryProbe(RecoveryProbe):
    def __init__(self, recovered: bool) -> None:
        self._recovered = recovered

    async def is_recovered(self, incident) -> bool:
        return self._recovered


class InMemoryRemediationPlanRepository(RemediationPlanRepository):
    def __init__(self) -> None:
        self._plans: dict[RemediationPlanId, RemediationPlan] = {}

    async def save(self, plan: RemediationPlan) -> None:
        self._plans[plan.id] = plan

    async def get(self, plan_id: RemediationPlanId) -> RemediationPlan | None:
        return self._plans.get(plan_id)

    async def exists_for_incident(self, incident_id: IncidentId) -> bool:
        return any(p.incident_id == incident_id for p in self._plans.values())


class FakeClusterClient(ClusterClient):
    """In-memory ClusterClient: a map of (namespace, name) -> replicas, recording every patch.

    A dry-run patch is recorded but never changes state; a non-dry-run patch that sets
    `spec.replicas` updates the stored count (so scale execute/undo are observable). Every fake
    deployment gets the synthetic selector `{"app": name}`, and `replica_sets` (keyed the same
    way) supplies the rollout history `list_replica_set_revisions` looks up by that selector —
    good enough for a test double without reimplementing real k8s label-selector matching.
    """

    def __init__(
        self,
        deployments: dict[tuple[str, str], int] | None = None,
        replica_sets: dict[tuple[str, str], list[ReplicaSetRevision]] | None = None,
    ) -> None:
        self._deployments = dict(deployments or {})
        self._replica_sets = dict(replica_sets or {})
        self.patches: list[tuple[str, str, dict, bool]] = []

    async def read_deployment(
        self, namespace: str, name: str
    ) -> DeploymentState | None:
        key = (namespace, name)
        if key not in self._deployments:
            return None
        return DeploymentState(namespace, name, self._deployments[key], {"app": name})

    async def patch_deployment(
        self, namespace: str, name: str, patch: dict, *, dry_run: bool
    ) -> DeploymentState:
        self.patches.append((namespace, name, patch, dry_run))
        replicas = self._deployments.get((namespace, name), 0)
        new_replicas = patch.get("spec", {}).get("replicas", replicas)
        if not dry_run:
            self._deployments[(namespace, name)] = new_replicas
        return DeploymentState(namespace, name, new_replicas, {"app": name})

    async def list_replica_set_revisions(
        self, namespace: str, selector: dict[str, str]
    ) -> list[ReplicaSetRevision]:
        name = selector.get("app", "")
        return list(self._replica_sets.get((namespace, name), []))


class FakeServiceGraph(ServiceGraph):
    def __init__(self, dependencies: dict[str, frozenset[str]] | None = None) -> None:
        self._dependencies = dependencies or {}

    def dependencies_of(self, service: str) -> frozenset[str]:
        return self._dependencies.get(service, frozenset())

    def dependents_of(self, service: str) -> frozenset[str]:
        return frozenset(
            caller
            for caller, callees in self._dependencies.items()
            if service in callees
        )


class FakeChangeLog(ChangeLog):
    def __init__(self, changes: list[ChangeEvent] | None = None) -> None:
        self._changes = changes or []

    async def recent_changes(self, service: str, since: datetime) -> list[ChangeEvent]:
        return [
            c
            for c in self._changes
            if c.service == service and c.occurred_at >= since
        ]


class FakeMetricsGateway(MetricsGateway):
    def __init__(
        self,
        error_ratios: dict[str, float] | None = None,
        gauges: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self._error_ratios = error_ratios or {}
        self._gauges = gauges or {}

    async def error_ratio(self, service: str, window: timedelta) -> float:
        return self._error_ratios.get(service, 0.0)

    async def gauge(self, service: str, metric: str) -> float | None:
        return self._gauges.get((service, metric))
