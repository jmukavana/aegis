from abc import ABC, abstractmethod


class ServiceGraph(ABC):
    """The service dependency graph used for topological correlation (ARCHITECTURE.md §3.3).

    Phase 1 backs this with a static/config adapter; Phase 2+ can derive it from Tempo traces
    behind this same port.
    """

    @abstractmethod
    def dependencies_of(self, service: str) -> frozenset[str]:
        """Services that `service` directly calls (its downstream dependencies)."""

    @abstractmethod
    def dependents_of(self, service: str) -> frozenset[str]:
        """Services that directly call `service` (its callers) — the reverse edges. Used for the
        blast-radius estimate: acting on `service` disrupts the things that depend on it."""
