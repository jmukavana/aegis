import pytest

from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import (
    Fingerprint,
    IncidentStatus,
    Severity,
)


def _signal() -> Signal:
    return Signal.observe(
        fingerprint=Fingerprint("checkout", "oom", "prod-ns"),
        source="kubernetes",
        severity=Severity.CRITICAL,
        summary="OOMKilled",
    )


def test_open_incident_seeds_fingerprint_and_first_signal():
    incident = Incident.open(title="checkout OOM", initial_signal=_signal())

    assert incident.status is IncidentStatus.OPEN
    assert incident.severity is Severity.CRITICAL
    assert str(incident.fingerprint) == "checkout/oom/prod-ns"
    assert len(incident.signals) == 1


def test_attach_adds_signal_without_mutating_original():
    incident = Incident.open(title="checkout OOM", initial_signal=_signal())

    updated = incident.attach(_signal())

    assert len(updated.signals) == 2
    assert len(incident.signals) == 1


def test_transition_changes_status_immutably():
    incident = Incident.open(title="checkout OOM", initial_signal=_signal())

    updated = incident.transition_to(IncidentStatus.DIAGNOSING)

    assert updated.status is IncidentStatus.DIAGNOSING
    assert incident.status is IncidentStatus.OPEN


def test_fingerprint_parse_roundtrip():
    fp = Fingerprint.parse("payments/crashloop/prod")
    assert (fp.service, fp.failure_class, fp.resource) == ("payments", "crashloop", "prod")
    assert str(fp) == "payments/crashloop/prod"


def test_fingerprint_parse_rejects_malformed():
    with pytest.raises(ValueError):
        Fingerprint.parse("payments:crashloop")
