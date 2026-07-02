from src.domain.topology.service_graph import ServiceGraph


class StaticServiceGraph(ServiceGraph):
    """Config-driven dependency graph.

    Phase 1 seed of the service map. Replace with a Tempo-trace-derived adapter later behind
    the same port — nothing else changes.
    """

    def __init__(self, dependencies: dict[str, frozenset[str]]) -> None:
        self._dependencies = dependencies

    def dependencies_of(self, service: str) -> frozenset[str]:
        return self._dependencies.get(service, frozenset())

    def dependents_of(self, service: str) -> frozenset[str]:
        return frozenset(
            caller
            for caller, callees in self._dependencies.items()
            if service in callees
        )
