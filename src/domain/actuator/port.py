from abc import ABC, abstractmethod

from src.domain.runbook.contract import ActionResult, DryRunResult, RunbookId


class Actuator(ABC):
    """The single seam between the Python "brain" and the cluster (ARCHITECTURE.md ADR-0001).

    The brain NEVER touches the Kubernetes API directly — it goes through this port. v1 backs
    it with a Python adapter; a Go actuator can replace the adapter later behind this same
    contract without touching detection, AI, or policy.
    """

    @abstractmethod
    async def dry_run(self, runbook_id: RunbookId, params: dict) -> DryRunResult: ...

    @abstractmethod
    async def execute(self, runbook_id: RunbookId, params: dict) -> ActionResult: ...

    @abstractmethod
    async def undo(self, runbook_id: RunbookId, params: dict) -> ActionResult: ...
