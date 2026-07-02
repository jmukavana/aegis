from src.infrastructure.kubernetes.event_watcher import event_to_signal


def test_known_reason_maps_to_failure_class():
    signal = event_to_signal(
        namespace="prod",
        name="checkout",
        reason="OOMKilling",
        message="Container checkout was OOMKilled",
    )

    assert signal is not None
    assert signal.source == "kubernetes"
    assert signal.fingerprint.service == "checkout"
    assert signal.fingerprint.failure_class == "oom"
    assert signal.fingerprint.resource == "prod"


def test_unknown_reason_is_ignored():
    assert (
        event_to_signal(namespace="prod", name="checkout", reason="Scheduled", message="")
        is None
    )


def test_control_plane_namespace_is_ignored():
    # A tracked reason in kube-system is still dropped — control-plane noise.
    assert (
        event_to_signal(
            namespace="kube-system",
            name="kube-scheduler",
            reason="Unhealthy",
            message="probe failed",
        )
        is None
    )
