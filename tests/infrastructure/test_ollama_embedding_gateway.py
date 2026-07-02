import httpx
import pytest

from src.infrastructure.llm.ollama_embedding_gateway import OllamaEmbeddingGateway


@pytest.mark.asyncio
async def test_embed_sends_input_and_returns_first_vector():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OllamaEmbeddingGateway("http://ollama:11434", "nomic-embed-text", client)

    vector = await gateway.embed("checkout is OOMing")

    assert captured["body"] == {"model": "nomic-embed-text", "input": "checkout is OOMing"}
    assert list(vector) == [0.1, 0.2, 0.3]
