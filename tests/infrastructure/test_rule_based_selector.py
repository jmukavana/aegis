from datetime import datetime, timezone
from uuid import uuid4

from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import Fingerprint, Severity, SignalId
from src.domain.remediation.environment import Environment
from src.infrastructure.remediation.rule_based_selector import (
    RuleBasedRemediationSelector,
)


def _incident(failure_class: str) -> Incident:
    signal = Signal(
        id=SignalId(uuid4()),
        fingerprint=Fingerprint("checkout", failure_class, "default"),
        source="kubernetes",
        severity=Severity.CRITICAL,
        summary="boom",
        observed_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    return Incident.open(title="x", initial_signal=signal)


def test_maps_crashloop_to_rolling_restart_with_params_from_fingerprint():
    candidate = RuleBasedRemediationSelector(Environment.LOCAL).select(
        _incident("crashloop")
    )
    assert candidate is not None
    assert str(candidate.runbook_id) == "rolling-restart"
    assert candidate.environment is Environment.LOCAL
    # service -> deployment, resource -> namespace
    assert candidate.params == {"namespace": "default", "deployment": "checkout"}


def test_returns_none_for_unmapped_failure_class():
    # No rule for a latency incident -> selector abstains, incident waits for a human.
    assert RuleBasedRemediationSelector(Environment.LOCAL).select(
        _incident("latency")
    ) is None
