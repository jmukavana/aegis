from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingGateway(ABC):
    """Port over the text-embedding model used for incident similarity search (Phase 2 RAG).

    Kept separate from the chat/analysis gateway because embedding is a distinct model with a
    distinct output (a fixed-length vector, not prose). The vector store (pgvector) depends on
    this to turn incident text into the vectors it compares — provider-swappable like every
    other adapter.
    """

    @abstractmethod
    async def embed(self, text: str) -> Sequence[float]: ...
