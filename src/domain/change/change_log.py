from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ChangeEvent:
    """A deploy / config / flag change — a first-class signal (most incidents follow one)."""

    service: str
    kind: str  # e.g. "deploy", "config", "flag"
    description: str
    occurred_at: datetime


class ChangeLog(ABC):
    """Port over recent change events, used for change attribution during correlation."""

    @abstractmethod
    async def recent_changes(self, service: str, since: datetime) -> list[ChangeEvent]:
        """Changes to `service` at or after `since`, most recent first."""
