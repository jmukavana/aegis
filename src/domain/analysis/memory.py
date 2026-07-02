from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.analysis.models import IncidentAnalysis
from src.domain.incident.entities import Incident


@dataclass(frozen=True)
class RecalledIncident:
    """A past incident surfaced by similarity search, with the diagnosis reached back then.

    Fed into the explainer prompt as *prior context only* — advisory, never authoritative:
    the stored `root_cause` may itself have been an LLM guess, so the prompt frames it as a
    hint, not ground truth. `similarity` is in [0, 1] (1 = identical). Read-only, like all of
    Phase 2 — recalling a past incident never re-runs whatever was proposed for it.
    """

    title: str
    fingerprint: str
    root_cause: str
    similarity: float


class IncidentMemory(ABC):
    """Port over the vector store of past incidents (Phase 2 retrieval-augmented explanation).

    `remember` embeds an incident and persists it alongside the analysis reached; `recall`
    returns the most semantically-similar past incidents to prime the next explanation. Kept
    behind a port so the embedding model and vector store (Ollama + pgvector today) are
    swappable without touching the application layer, exactly like the analysis gateway.
    """

    @abstractmethod
    async def remember(self, incident: Incident, analysis: IncidentAnalysis) -> None: ...

    @abstractmethod
    async def recall(self, incident: Incident, limit: int) -> list[RecalledIncident]: ...
