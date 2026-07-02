# Phase 1 seed of the service dependency map (which service calls which). Used for
# topological correlation: a signal on `checkout` can attach to an open `payments` incident.
# Replace with a Tempo-trace-derived ServiceGraph later behind the same port.
SERVICE_DEPENDENCIES: dict[str, frozenset[str]] = {
    "checkout": frozenset({"payments", "inventory"}),
    "payments": frozenset({"ledger"}),
}
