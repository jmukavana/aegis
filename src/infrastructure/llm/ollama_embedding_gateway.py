from collections.abc import Sequence

import httpx

from src.domain.analysis.embedding import EmbeddingGateway


class OllamaEmbeddingGateway(EmbeddingGateway):
    """Local-embedding adapter (Ollama `/api/embed`) for the embedding port.

    Uses a dedicated embedding model (e.g. `nomic-embed-text`, 768-d) rather than the chat
    model — embeddings and chat are separate models in Ollama. Provider-neutral over httpx;
    swap this adapter to hit any other embedding backend without touching the vector store.
    """

    def __init__(
        self, base_url: str, model: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=60.0)

    async def embed(self, text: str) -> Sequence[float]:
        response = await self._client.post(
            f"{self._base_url}/api/embed",
            json={"model": self._model, "input": text},
        )
        response.raise_for_status()
        # /api/embed returns {"embeddings": [[...]]} — one vector per input; we send one.
        return response.json()["embeddings"][0]
