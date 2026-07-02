from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from src.domain.remediation.environment import Environment
from src.domain.runbook.contract import RiskTier, RunbookId


class PolicyOutcome(str, Enum):
    """The three verdicts of the decision gate (ARCHITECTURE.md §3.4)."""

    ALLOW_AUTO = "allow_auto"  # reserved for Phase 4 — never returned while auto-execute is off
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyInput:
    """The facts the policy gate decides on — deliberately small and provider-agnostic.

    `reversible` and `confidence` come from the dry-run + the analysis; `success_rate` and
    `sample_size` are the runbook's *verified* track record for this fingerprint (Phase 4 trust
    ratchet). The engine never sees the LLM, the cluster, or free-form text, only these facts.
    """

    runbook_id: RunbookId
    environment: Environment
    risk_tier: RiskTier
    reversible: bool
    confidence: float
    success_rate: float = 0.0
    sample_size: int = 0
    blast_radius: int = 0  # # of downstream services disrupted (Phase 4c)


@dataclass(frozen=True)
class PolicyDecision:
    """The gate's verdict. `required_permission`, when set, is the RBAC permission an approver
    must hold to satisfy this decision (checked in Phase 3b's approval flow)."""

    outcome: PolicyOutcome
    reasons: tuple[str, ...]
    required_permission: str | None = None


class PolicyEngine(ABC):
    """The decision gate: *may this action run, here, now?* — the only place that answers it.

    The LLM proposes and a human approves, but this engine independently decides what is even
    eligible. Kept behind a port so the embedded Python rules (v1, ADR-0002) can be replaced by
    OPA/Rego later without touching the application layer — the same swap discipline as the
    `Actuator`.
    """

    @abstractmethod
    def evaluate(self, request: PolicyInput) -> PolicyDecision: ...
