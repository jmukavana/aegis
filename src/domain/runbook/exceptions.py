from src.domain.runbook.contract import RunbookId


class RunbookNotFoundError(Exception):
    """Raised when a runbook id has no executable implementation in the registry.

    The Phase 2 catalog enum already stops the LLM naming a non-runbook; this is the
    executable-side twin — the actuator refuses an id it can't map to real, reviewed code.
    """

    def __init__(self, runbook_id: RunbookId) -> None:
        super().__init__(f"No executable runbook registered for id '{runbook_id}'")
        self.runbook_id = runbook_id
