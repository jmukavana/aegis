from src.domain.remediation.environment import Environment
from src.domain.remediation.policy import PolicyInput, PolicyOutcome
from src.domain.runbook.contract import RiskTier, RunbookId
from src.infrastructure.policy.rule_based_policy import (
    AutoThreshold,
    RuleBasedPolicyEngine,
)


def _input(
    *,
    environment: Environment,
    risk: RiskTier,
    reversible: bool = True,
    success_rate: float = 0.0,
    sample_size: int = 0,
    blast_radius: int = 0,
) -> PolicyInput:
    return PolicyInput(
        runbook_id=RunbookId("rolling-restart"),
        environment=environment,
        risk_tier=risk,
        reversible=reversible,
        confidence=0.9,
        success_rate=success_rate,
        sample_size=sample_size,
        blast_radius=blast_radius,
    )


LOCAL_THRESHOLD = AutoThreshold(min_success_rate=0.8, min_samples=2, max_blast_radius=3)
STAGING_THRESHOLD = AutoThreshold(min_success_rate=0.97, min_samples=20, max_blast_radius=1)


def _auto_engine(
    thresholds: dict[Environment, AutoThreshold] | None = None,
) -> RuleBasedPolicyEngine:
    return RuleBasedPolicyEngine(
        allow_auto=True, auto_thresholds=thresholds or {Environment.LOCAL: LOCAL_THRESHOLD}
    )


def test_prod_is_denied_in_phase_3():
    decision = RuleBasedPolicyEngine().evaluate(
        _input(environment=Environment.PROD, risk=RiskTier.LOW)
    )
    assert decision.outcome is PolicyOutcome.DENY
    assert "prod" in decision.reasons[0]


def test_high_risk_is_denied():
    decision = RuleBasedPolicyEngine().evaluate(
        _input(environment=Environment.STAGING, risk=RiskTier.HIGH)
    )
    assert decision.outcome is PolicyOutcome.DENY


def test_low_risk_reversible_requires_env_scoped_approval():
    decision = RuleBasedPolicyEngine().evaluate(
        _input(environment=Environment.STAGING, risk=RiskTier.LOW)
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.required_permission == "action:execute:staging"


def test_irreversible_action_demands_elevated_permission():
    decision = RuleBasedPolicyEngine().evaluate(
        _input(environment=Environment.LOCAL, risk=RiskTier.MEDIUM, reversible=False)
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.required_permission == "action:execute:local:irreversible"


def test_auto_execute_is_never_returned_while_disabled():
    # Even the safest, most-proven action needs a human while auto is off (read-only default).
    decision = RuleBasedPolicyEngine(allow_auto=False).evaluate(
        _input(environment=Environment.LOCAL, risk=RiskTier.LOW, success_rate=1.0, sample_size=99)
    )
    assert decision.outcome is not PolicyOutcome.ALLOW_AUTO


def test_earned_trust_allows_auto():
    decision = _auto_engine().evaluate(
        _input(environment=Environment.LOCAL, risk=RiskTier.LOW, success_rate=1.0, sample_size=3)
    )
    assert decision.outcome is PolicyOutcome.ALLOW_AUTO
    assert decision.required_permission is None


def test_not_enough_samples_still_requires_approval():
    decision = _auto_engine().evaluate(
        _input(environment=Environment.LOCAL, risk=RiskTier.LOW, success_rate=1.0, sample_size=1)
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL


def test_low_success_rate_still_requires_approval():
    decision = _auto_engine().evaluate(
        _input(environment=Environment.LOCAL, risk=RiskTier.LOW, success_rate=0.5, sample_size=10)
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL


def test_auto_never_applies_to_environment_with_no_threshold_configured():
    # staging has no AutoThreshold in this engine (only local) — even fully proven, needs approval.
    decision = _auto_engine().evaluate(
        _input(environment=Environment.STAGING, risk=RiskTier.LOW, success_rate=1.0, sample_size=9)
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL


# --- staged promotion: staging is a stricter, separately-configured tier ----------------

def test_staging_track_record_that_would_earn_local_auto_is_not_enough_for_staging():
    engine = _auto_engine({Environment.LOCAL: LOCAL_THRESHOLD, Environment.STAGING: STAGING_THRESHOLD})
    # Clears local's bar (0.8/2) but not staging's much stricter one (0.97/20).
    decision = engine.evaluate(
        _input(environment=Environment.STAGING, risk=RiskTier.LOW, success_rate=0.85, sample_size=5)
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL


def test_staging_auto_executes_once_it_clears_its_own_stricter_bar():
    engine = _auto_engine({Environment.LOCAL: LOCAL_THRESHOLD, Environment.STAGING: STAGING_THRESHOLD})
    decision = engine.evaluate(
        _input(
            environment=Environment.STAGING, risk=RiskTier.LOW,
            success_rate=0.98, sample_size=25, blast_radius=1,
        )
    )
    assert decision.outcome is PolicyOutcome.ALLOW_AUTO


def test_each_environment_thresholds_independently_even_for_the_same_runbook():
    # Same track record qualifies for local's looser bar but not staging's stricter one.
    engine = _auto_engine({Environment.LOCAL: LOCAL_THRESHOLD, Environment.STAGING: STAGING_THRESHOLD})
    shared = dict(risk=RiskTier.LOW, success_rate=0.9, sample_size=10)
    assert engine.evaluate(_input(environment=Environment.LOCAL, **shared)).outcome is (
        PolicyOutcome.ALLOW_AUTO
    )
    assert engine.evaluate(_input(environment=Environment.STAGING, **shared)).outcome is (
        PolicyOutcome.REQUIRE_APPROVAL
    )


def test_wide_blast_radius_blocks_auto_even_when_trusted():
    # Fully proven + low-risk + reversible + local, but disrupts 5 downstream services (> cap 3).
    decision = _auto_engine().evaluate(
        _input(
            environment=Environment.LOCAL, risk=RiskTier.LOW,
            success_rate=1.0, sample_size=9, blast_radius=5,
        )
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert any("blast radius" in r for r in decision.reasons)


def test_within_blast_radius_cap_still_auto():
    decision = _auto_engine().evaluate(
        _input(
            environment=Environment.LOCAL, risk=RiskTier.LOW,
            success_rate=1.0, sample_size=9, blast_radius=3,
        )
    )
    assert decision.outcome is PolicyOutcome.ALLOW_AUTO


def test_medium_risk_runbook_never_earns_auto_even_with_perfect_track_record():
    # rollback-deployment is MEDIUM risk (Phase 4c) — auto is LOW-risk only, so a perfect,
    # ample track record still lands on REQUIRE_APPROVAL, never ALLOW_AUTO.
    decision = _auto_engine().evaluate(
        _input(
            environment=Environment.LOCAL, risk=RiskTier.MEDIUM,
            success_rate=1.0, sample_size=999, blast_radius=0,
        )
    )
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
