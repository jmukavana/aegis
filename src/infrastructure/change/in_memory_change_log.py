from datetime import datetime

from src.domain.change.change_log import ChangeEvent, ChangeLog


class InMemoryChangeLog(ChangeLog):
    """Phase 1 change log fed in-process (e.g. from a CI/CD webhook handler later).

    Kept deliberately simple; a durable adapter (Postgres-backed) can replace it behind the
    same port when change history needs to survive restarts.
    """

    def __init__(self) -> None:
        self._changes: list[ChangeEvent] = []

    def record(self, change: ChangeEvent) -> None:
        self._changes.append(change)

    async def recent_changes(self, service: str, since: datetime) -> list[ChangeEvent]:
        matching = [
            change
            for change in self._changes
            if change.service == service and change.occurred_at >= since
        ]
        return sorted(matching, key=lambda change: change.occurred_at, reverse=True)
