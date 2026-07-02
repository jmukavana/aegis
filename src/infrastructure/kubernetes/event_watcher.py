"""Kubernetes event watcher (Phase 1).

Turns cluster events (OOMKilled, CrashLoopBackOff, Pending, ...) into `ObservedSignal`s and
feeds them through correlation. The event->signal *mapping* is pure and unit-tested; the
streaming part needs a live cluster, so it is marked `pragma: no cover` and only runs when
`Settings.kube_context` is set (Phase 0/tests boot fine with no cluster).
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator

from src.application.incident.use_cases import ObservedSignal
from src.domain.incident.value_objects import Fingerprint, Severity

# Maps a Kubernetes event `reason` to our failure-class taxonomy.
EVENT_REASON_TO_FAILURE_CLASS: dict[str, str] = {
    "OOMKilling": "oom",
    "BackOff": "crashloop",
    "Failed": "failed",
    "FailedScheduling": "pending",
    "Unhealthy": "unhealthy",
}

# Control-plane namespaces emit constant health-probe noise (esp. `Unhealthy`); Aegis
# watches workload namespaces, not the cluster's own components.
IGNORED_NAMESPACES: frozenset[str] = frozenset(
    {"kube-system", "kube-public", "kube-node-lease"}
)


def event_to_signal(
    *, namespace: str, name: str, reason: str, message: str
) -> ObservedSignal | None:
    """Translate one k8s event into an ObservedSignal, or None if we skip it.

    Skipped when the reason isn't one we track, or the namespace is an ignored
    control-plane namespace.
    """
    if namespace in IGNORED_NAMESPACES:
        return None

    failure_class = EVENT_REASON_TO_FAILURE_CLASS.get(reason)
    if failure_class is None:
        return None

    return ObservedSignal(
        fingerprint=Fingerprint(
            service=name, failure_class=failure_class, resource=namespace
        ),
        source="kubernetes",
        severity=Severity.WARNING,
        summary=message,
    )


def _iter_event_signals(kube_context: str) -> Iterator[ObservedSignal]:  # pragma: no cover - needs a cluster
    """Blocking generator over cluster events. Runs in a worker thread (see run_event_watcher)."""
    from kubernetes import client, config, watch

    if kube_context:
        config.load_kube_config(context=kube_context)
    else:
        config.load_incluster_config()

    core = client.CoreV1Api()
    stream = watch.Watch().stream(core.list_event_for_all_namespaces)
    for event in stream:
        obj = event["object"]
        signal = event_to_signal(
            namespace=obj.metadata.namespace or "default",
            name=obj.involved_object.name or "unknown",
            reason=obj.reason or "",
            message=obj.message or "",
        )
        if signal is not None:
            yield signal


async def run_event_watcher(
    kube_context: str, on_signal: Callable[[ObservedSignal], Awaitable[None]]
) -> None:  # pragma: no cover - needs a cluster
    """Bridge the synchronous k8s Watch into the async world.

    The blocking `_iter_event_signals` runs in a thread executor and hands each signal back to
    the event loop, where `on_signal` (wired to CorrelateSignal in the composition root)
    consumes it.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[ObservedSignal] = asyncio.Queue()

    def produce() -> None:
        for signal in _iter_event_signals(kube_context):
            asyncio.run_coroutine_threadsafe(queue.put(signal), loop)

    loop.run_in_executor(None, produce)
    while True:
        await on_signal(await queue.get())
