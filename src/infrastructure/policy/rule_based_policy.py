from dataclasses import dataclass

from src.domain.remediation.environment import Environment
from src.domain.remediation.policy import (
    PolicyDecision,
    PolicyEngine,
    PolicyInput,
    PolicyOutcome,
)
from src.domain.runbook.contract import RiskTier


@dataclass(frozen=True)
class AutoThreshold:
    """The trust bar a runbook must clear to auto-execute in one environment.

    Staged promotion (Phase 4c): each auto-enabled environment gets its own bar, stricter the
    closer to prod — `staging` demands a much longer, cleaner track record and a tighter blast
    radius than `local`. An environment simply absent from the engine's threshold map can never
    auto-execute, no matter the track record.
    """

    min_success_rate: float
    min_samples: int
    max_blast_radius: int


class RuleBasedPolicyEngine(PolicyEngine):
    """Embedded Python policy gate (ADR-0002, decision 1).

    Rule set:
      * `PROD`            -> DENY (no prod actuation anywhere in Phase 3/4 — never in the
        threshold map below, so this rule can't be bypassed by config)
      * `risk == HIGH`    -> DENY (node ops etc. stay manual, out-of-band)
      * **earned auto** (Phase 4): if `allow_auto` and the environment has an `AutoThreshold`
        configured and the action is LOW-risk + reversible and its *verified* success rate for
        this fingerprint clears that environment's threshold over enough samples, within its
        blast-radius cap -> `ALLOW_AUTO` (no human needed). Staged promotion: an environment
        only earns a place in the map once its bar is set deliberately (e.g. `staging` starts
        absent, then is added with a much stricter `AutoThreshold` than `local`).
      * `reversible False` -> REQUIRE_APPROVAL with an *elevated* execute permission
      * everything else    -> REQUIRE_APPROVAL against `action:execute:<env>`

    `allow_auto` defaults off (the read-only default from §5.1) — even Phase 4 starts with a
    human on every action until autonomy is explicitly turned on. Rego swaps in later behind the
    `PolicyEngine` port; none of the above changes callers.
    """

    def __init__(
        self,
        *,
        allow_auto: bool = False,
        auto_thresholds: dict[Environment, AutoThreshold] | None = None,
    ) -> None:
        self._allow_auto = allow_auto
        self._auto_thresholds = auto_thresholds or {}

    def evaluate(self, request: PolicyInput) -> PolicyDecision:
        if request.environment is Environment.PROD:
            return PolicyDecision(
                PolicyOutcome.DENY,
                ("prod actuation is disabled in Phase 3/4",),
            )
        if request.risk_tier is RiskTier.HIGH:
            return PolicyDecision(
                PolicyOutcome.DENY,
                (f"{request.risk_tier.value}-risk actions must be run manually, out of band",),
            )

        threshold = self._auto_thresholds.get(request.environment)
        if threshold is not None and self._meets(request, threshold):
            return PolicyDecision(
                PolicyOutcome.ALLOW_AUTO,
                (
                    f"earned auto-execute: {request.runbook_id} has "
                    f"{request.success_rate:.0%} verified success over {request.sample_size} "
                    f"runs in {request.environment.value} (>= {threshold.min_success_rate:.0%}/"
                    f"{threshold.min_samples}, blast radius {request.blast_radius} <= "
                    f"{threshold.max_blast_radius})",
                ),
            )

        permission = f"action:execute:{request.environment.value}"
        reasons = [
            f"{request.risk_tier.value}-risk action in {request.environment.value} "
            "requires human approval",
        ]
        if not request.reversible:
            permission = f"{permission}:irreversible"
            reasons.append("action has no defined undo — elevated approval required")
        # Explain a near-miss on auto: trusted, but too widely-connected to act unattended.
        if threshold is not None and self._trusted_but_wide_blast_radius(request, threshold):
            reasons.append(
                f"blast radius {request.blast_radius} exceeds auto cap "
                f"{threshold.max_blast_radius} for {request.environment.value} — "
                "a human must approve"
            )

        return PolicyDecision(
            PolicyOutcome.REQUIRE_APPROVAL, tuple(reasons), permission
        )

    def _meets(self, request: PolicyInput, threshold: AutoThreshold) -> bool:
        return (
            self._allow_auto
            and request.risk_tier is RiskTier.LOW
            and request.reversible
            and request.sample_size >= threshold.min_samples
            and request.success_rate >= threshold.min_success_rate
            and request.blast_radius <= threshold.max_blast_radius
        )

    def _trusted_but_wide_blast_radius(
        self, request: PolicyInput, threshold: AutoThreshold
    ) -> bool:
        """True when blast radius is the only thing standing between this action and auto."""
        return (
            self._allow_auto
            and request.risk_tier is RiskTier.LOW
            and request.reversible
            and request.sample_size >= threshold.min_samples
            and request.success_rate >= threshold.min_success_rate
            and request.blast_radius > threshold.max_blast_radius
        )
