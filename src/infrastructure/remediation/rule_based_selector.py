from src.domain.incident.entities import Incident
from src.domain.remediation.environment import Environment
from src.domain.remediation.selector import RemediationCandidate, RemediationSelector
from src.domain.runbook.contract import RunbookId

# Maps an incident's failure class to the runbook that addresses it. Only the lowest-risk,
# reversible actions map here — matching the Phase 3c/4a execution allowlist. Anything not
# listed selects nothing (the incident waits for a human).
DEFAULT_FAILURE_CLASS_RUNBOOK: dict[str, str] = {
    "crashloop": "rolling-restart",
    "oom": "rolling-restart",
}


class RuleBasedRemediationSelector(RemediationSelector):
    """Fingerprint → runbook by rule.

    Params come straight from the fingerprint, which for k8s-sourced signals is
    `service/failure_class/resource` with `service` = deployment and `resource` = namespace (see
    the event watcher). The environment is fixed by config — the fingerprint's `resource` is a
    namespace, not an environment, so the two are not conflated.
    """

    def __init__(
        self,
        environment: Environment,
        mapping: dict[str, str] = DEFAULT_FAILURE_CLASS_RUNBOOK,
    ) -> None:
        self._environment = environment
        self._mapping = mapping

    def select(self, incident: Incident) -> RemediationCandidate | None:
        runbook = self._mapping.get(incident.fingerprint.failure_class)
        if runbook is None:
            return None
        return RemediationCandidate(
            runbook_id=RunbookId(runbook),
            environment=self._environment,
            params={
                "namespace": incident.fingerprint.resource,
                "deployment": incident.fingerprint.service,
            },
        )
