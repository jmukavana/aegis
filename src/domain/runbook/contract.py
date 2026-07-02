from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import NewType

RunbookId = NewType("RunbookId", str)


class RiskTier(str, Enum):
    """Higher tiers demand higher confidence + explicit approval (ARCHITECTURE.md §5)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ActionResult:
    succeeded: bool
    detail: str


@dataclass(frozen=True)
class DryRunResult:
    """The predicted effect of a runbook, surfaced for approval before real execution."""

    predicted_effect: str
    reversible: bool


class Runbook(ABC):
    """A remediation action as versioned, reviewed code — never LLM improvisation.

    Phase 0 defines the contract only; concrete runbooks (rolling-restart, scale, rollback)
    are implemented in Phase 3 behind the guardrail/policy machinery.
    """

    id: RunbookId
    risk_tier: RiskTier

    @abstractmethod
    async def dry_run(self, params: dict) -> DryRunResult:
        """Predict the effect without mutating anything."""

    @abstractmethod
    async def execute(self, params: dict) -> ActionResult:
        """Perform the (idempotent) remediation."""

    @abstractmethod
    async def undo(self, params: dict) -> ActionResult:
        """Reverse the remediation where possible; used for auto-rollback."""


class RunbookRegistry(ABC):
    """Port over the catalog of available runbooks (the LLM's tool catalog in Phase 2)."""

    @abstractmethod
    def get(self, runbook_id: RunbookId) -> Runbook | None: ...

    @abstractmethod
    def list_all(self) -> list[Runbook]: ...
