from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.runbook.contract import RunbookId


@dataclass(frozen=True)
class OutcomeStats:
    """A runbook's track record for one incident fingerprint — the trust the ratchet reads."""

    sample_size: int
    success_rate: float


class OutcomeStore(ABC):
    """Records how a runbook fared for a given incident fingerprint — the trust flywheel.

    Every *verified* remediation records success/failure keyed by (runbook, fingerprint). Phase 4
    reads `stats` to let an action *earn* auto-execute: a runbook graduates from propose-only to
    auto only once its verified success rate for that signature clears the policy threshold over
    enough samples (ARCHITECTURE.md §3.6/§5). Written by `VerifyRemediation`, never by execution
    alone.
    """

    @abstractmethod
    async def record(self, runbook_id: RunbookId, fingerprint: str, success: bool) -> None: ...

    @abstractmethod
    async def stats(self, runbook_id: RunbookId, fingerprint: str) -> OutcomeStats: ...

    async def success_rate(self, runbook_id: RunbookId, fingerprint: str) -> float:
        return (await self.stats(runbook_id, fingerprint)).success_rate
