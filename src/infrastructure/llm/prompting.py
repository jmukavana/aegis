"""Prompt, response-schema, and parsing logic shared by every LLM adapter.

The incident-analysis *contract* with the model — the system instruction, how an incident +
runbook menu + recalled history is rendered, the JSON schema that constrains `runbook_id` to
the catalog enum, and how the reply is parsed back into the domain — is identical whichever
provider serves it (Ollama, Vertex/Llama, …). Keeping it here means a new provider adapter is
just "post these messages, get JSON back", and the safety-critical enum constraint lives in one
place.
"""

import json

from src.domain.analysis.memory import RecalledIncident
from src.domain.analysis.models import IncidentAnalysis, RemediationProposal
from src.domain.incident.entities import Incident
from src.domain.runbook.contract import RunbookId
from src.domain.runbook.spec import RunbookSpec

SYSTEM_PROMPT = (
    "You are an SRE incident analyst. Explain the incident and propose remediations, "
    "each referencing one of the provided runbooks by id. You never invent commands or "
    "runbook ids — only the ids listed are valid. Rank proposals best-first and give an "
    "honest confidence in [0,1]. This is advisory only; nothing you propose is executed."
)


def analysis_schema(catalog: list[RunbookSpec]) -> dict:
    """JSON schema for the reply; `runbook_id` is an enum of catalog ids so the model can only
    reference pre-vetted runbooks (ARCHITECTURE.md §3.4 LLM boundary)."""
    runbook_ids = [str(spec.id) for spec in catalog]
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "root_cause": {"type": "string"},
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "runbook_id": {"type": "string", "enum": runbook_ids},
                        "parameters": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "runbook_id",
                        "parameters",
                        "reasoning",
                        "confidence",
                    ],
                },
            },
        },
        "required": ["summary", "root_cause", "proposals"],
    }


def incident_prompt(
    incident: Incident, catalog: list[RunbookSpec], similar: list[RecalledIncident]
) -> str:
    signals = "\n".join(
        f"  - [{s.severity.value}] {s.summary} (from {s.source})"
        for s in incident.signals
    )
    runbooks = "\n".join(
        f"  - {spec.id} (risk={spec.risk_tier.value}, params={', '.join(spec.parameters)}): "
        f"{spec.description}"
        for spec in catalog
    )
    change = incident.suspected_change or "none detected"
    return (
        f"Incident: {incident.title}\n"
        f"Fingerprint: {incident.fingerprint}\n"
        f"Severity: {incident.severity.value}\n"
        f"Suspected recent change: {change}\n"
        f"Signals ({len(incident.signals)}):\n{signals}\n\n"
        f"{_similar_section(similar)}"
        f"Available runbooks:\n{runbooks}"
    )


def _similar_section(similar: list[RecalledIncident]) -> str:
    if not similar:
        return ""
    lines = "\n".join(
        f"  - {r.title} ({r.fingerprint}, similarity {r.similarity:.2f}): {r.root_cause}"
        for r in similar
    )
    return (
        "Similar past incidents and the root cause diagnosed then "
        "(advisory hints, not ground truth):\n"
        f"{lines}\n\n"
    )


def parse_analysis(content: str) -> IncidentAnalysis:
    payload = json.loads(content)
    proposals = tuple(
        RemediationProposal(
            runbook_id=RunbookId(p["runbook_id"]),
            parameters=p.get("parameters", ""),
            reasoning=p.get("reasoning", ""),
            confidence=float(p.get("confidence", 0.0)),
        )
        for p in payload.get("proposals", [])
    )
    return IncidentAnalysis(
        summary=payload.get("summary", ""),
        root_cause=payload.get("root_cause", ""),
        proposals=proposals,
    )
