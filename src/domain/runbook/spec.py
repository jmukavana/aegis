from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.runbook.contract import RiskTier, RunbookId


@dataclass(frozen=True)
class RunbookSpec:
    """Catalog metadata for a remediation — what the LLM sees and may reference.

    This is the *description* of a runbook, not an executable one. Phase 2's LLM proposes a
    `runbook_id` from this catalog; Phase 3 maps the id to an executable `Runbook` behind the
    guardrail/policy machinery. Keeping the catalog (specs) separate from execution keeps the
    read-only planner honest — it can only name pre-vetted actions.
    """

    id: RunbookId
    description: str
    risk_tier: RiskTier
    parameters: tuple[str, ...]


class RunbookCatalog(ABC):
    """Port over the catalog of available runbook specs (the LLM's tool menu)."""

    @abstractmethod
    def list_specs(self) -> list[RunbookSpec]: ...

    @abstractmethod
    def get_spec(self, runbook_id: RunbookId) -> RunbookSpec | None: ...
