from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.incident.entities import Incident
from src.domain.remediation.environment import Environment
from src.domain.runbook.contract import RunbookId


@dataclass(frozen=True)
class RemediationCandidate:
    """A deterministically-chosen remediation for an incident (Phase 4b autonomous trigger)."""

    runbook_id: RunbookId
    environment: Environment
    params: dict


class RemediationSelector(ABC):
    """Chooses *which* pre-vetted runbook to try for an incident, with no human and no LLM.

    Autonomous *selection* is kept deterministic on purpose: the LLM stays advisory (the
    `/explain` path), while the closed loop maps an incident to a runbook by rule. Safety does
    not rest on this being right — the policy trust-ratchet still gates execution, so a wrong or
    absent selection only ever yields a pending plan a human reviews, never an unearned action.
    Returns `None` when no runbook applies.
    """

    @abstractmethod
    def select(self, incident: Incident) -> RemediationCandidate | None: ...
