from src.domain.topology.service_graph import ServiceGraph


def blast_radius(graph: ServiceGraph, service: str) -> frozenset[str]:
    """The transitive dependents of `service` — everything disrupted if we act on it.

    Restarting/scaling a service briefly perturbs it, which ripples out to its callers, and
    theirs. This is the set the policy gate sizes up before allowing an autonomous action: a
    highly-connected service needs a human even for a trusted runbook (ARCHITECTURE.md §3.4).
    Excludes `service` itself; safe on cycles.
    """
    affected: set[str] = set()
    frontier = [service]
    while frontier:
        current = frontier.pop()
        for dependent in graph.dependents_of(current):
            if dependent not in affected:
                affected.add(dependent)
                frontier.append(dependent)
    return frozenset(affected)
