from dataclasses import dataclass

from src.domain.runbook.contract import RunbookId


@dataclass(frozen=True)
class RemediationProposal:
    """An LLM proposal: a reference to a pre-vetted runbook, never a raw command.

    `confidence` is the model's own estimate (0..1). Phase 3's policy engine — not the model
    — decides whether a proposal may auto-execute; here it is advisory only.
    """

    runbook_id: RunbookId
    parameters: str
    reasoning: str
    confidence: float


@dataclass(frozen=True)
class IncidentAnalysis:
    """The read-only output of the Phase 2 explainer: a diagnosis plus ranked proposals."""

    summary: str
    root_cause: str
    proposals: tuple[RemediationProposal, ...]
