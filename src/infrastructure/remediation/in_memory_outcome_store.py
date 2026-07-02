from collections import defaultdict

from src.domain.remediation.outcome import OutcomeStats, OutcomeStore
from src.domain.runbook.contract import RunbookId


class InMemoryOutcomeStore(OutcomeStore):
    """In-process success/failure tally per (runbook, fingerprint) — the flywheel's memory.

    A durable store (Postgres) replaces it behind the port for Phase 4c, where the success rate
    gates auto-execute across restarts and replicas.
    """

    def __init__(self) -> None:
        self._tally: dict[tuple[str, str], list[bool]] = defaultdict(list)

    async def record(
        self, runbook_id: RunbookId, fingerprint: str, success: bool
    ) -> None:
        self._tally[(str(runbook_id), fingerprint)].append(success)

    async def stats(self, runbook_id: RunbookId, fingerprint: str) -> OutcomeStats:
        results = self._tally[(str(runbook_id), fingerprint)]
        if not results:
            return OutcomeStats(sample_size=0, success_rate=0.0)  # unproven — no history yet
        return OutcomeStats(
            sample_size=len(results), success_rate=sum(results) / len(results)
        )
