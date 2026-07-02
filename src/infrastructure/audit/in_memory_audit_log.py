from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.audit import AuditEntry, AuditLog


class InMemoryAuditLog(AuditLog):
    """Append-only in-memory audit trail (Phase 3b groundwork).

    Swapped for a Postgres-backed append-only table when the pipeline needs durable audit;
    the port is what the application depends on, so that swap won't touch use cases.
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    async def append(self, entry: AuditEntry) -> None:
        self._entries.append(entry)

    async def for_incident(self, incident_id: IncidentId) -> list[AuditEntry]:
        return [e for e in self._entries if e.incident_id == incident_id]
