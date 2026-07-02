from dataclasses import dataclass
from enum import Enum
from typing import NewType
from uuid import UUID

IncidentId = NewType("IncidentId", UUID)
SignalId = NewType("SignalId", UUID)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    OPEN = "open"
    DIAGNOSING = "diagnosing"
    REMEDIATING = "remediating"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class Fingerprint:
    """Stable identity of a failure: `service + failure-class + resource`.

    Signals sharing a fingerprint within a correlation window collapse into one incident
    (see ARCHITECTURE.md §3.3). Phase 0 only stores it; correlation arrives in Phase 1.
    """

    service: str
    failure_class: str
    resource: str

    def __post_init__(self) -> None:
        if not self.service or not self.failure_class or not self.resource:
            raise ValueError(f"Fingerprint requires all three parts, got {self!r}")

    @classmethod
    def parse(cls, value: str) -> "Fingerprint":
        parts = value.split("/")
        if len(parts) != 3:
            raise ValueError(
                f"Fingerprint must be 'service/failure_class/resource', got {value!r}"
            )
        return cls(service=parts[0], failure_class=parts[1], resource=parts[2])

    def __str__(self) -> str:
        return f"{self.service}/{self.failure_class}/{self.resource}"
