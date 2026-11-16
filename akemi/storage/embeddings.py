"""Embeddings: transformam texto em vetores para a busca por significado.

Com eles, "meu cachorro" acha a lembrança "levei o Thor no veterinário",
coisa que a busca por palavra não acha. Usa a rota ``/embeddings`` compatível
com OpenAI, que NIM, OpenAI, Ollama (``/v1``) e o servidor do llama.cpp
(``--embeddings``) entendem. Só ``httpx`` + ``numpy``, sem SDK.

Tudo é opcional: sem embedder configurado (``MEMORY_EMBED_PROVIDER`` vazio) a
memória continua com a busca por palavra de sempre.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import httpx
import numpy as np

if TYPE_CHECKING:
    from akemi.core.config import Settings

Purpose = Literal["query", "passage"]


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    """Interface: ``await embed(textos)`` devolve um vetor normalizado por texto."""

    name = "embedder"
    model = ""

    async def embed(self, texts: list[str], purpose: Purpose = "passage") -> list[np.ndarray]:
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover - nada a fechar por padrão
        pass


def normalize(vec) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


class OpenAICompatibleEmbedder(Embedder):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        name: str = "openai-compatible",
        timeout: float = 15.0,
        input_type: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.model = model
        # Os modelos de embedding da NVIDIA pedem "query" ou "passage".
        self._input_type = input_type
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )

    async def embed(self, texts: list[str], purpose: Purpose = "passage") -> list[np.ndarray]:
        if not texts:
            return []
        body: dict = {"model": self.model, "input": [t[:4000] for t in texts]}
        if self._input_type:
            body["input_type"] = purpose
            body["truncate"] = "END"
        try:
            resp = await self._client.post("/embeddings", json=body)
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"{self.name}: {exc}") from exc
        if resp.status_code >= 400:
            raise EmbeddingError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
            vectors = [normalize(d["embedding"]) for d in data]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"{self.name}: resposta inesperada") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(f"{self.name}: pedi {len(texts)} vetores e vieram {len(vectors)}")
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


DEFAULT_MODELS = {
    "nim": "nvidia/nv-embedqa-e5-v5",
    "openai": "text-embedding-3-small",
    "ollama": "nomic-embed-text",
    "llamacpp": "default",
}


def build_embedder(settings: "Settings") -> Embedder | None:
    """O embedder de ``MEMORY_EMBED_PROVIDER`` (``None`` se desligado ou sem chave)."""
    name = settings.memory_embed_provider
    if not name:
        return None
    model = settings.memory_embed_model or DEFAULT_MODELS[name]
    t = settings.memory_embed_timeout_s
    if name == "nim":
        if not settings.nvidia_api_key:
            return None
        return OpenAICompatibleEmbedder(
            settings.nim_base_url, model, settings.nvidia_api_key, name="nim", timeout=t, input_type=True
        )
    if name == "openai":
        if not settings.openai_api_key:
            return None
        return OpenAICompatibleEmbedder(
            settings.openai_base_url, model, settings.openai_api_key, name="openai", timeout=t
        )
    if name == "ollama":
        return OpenAICompatibleEmbedder(settings.ollama_base_url, model, name="ollama", timeout=t)
    if name == "llamacpp":
        return OpenAICompatibleEmbedder(settings.llamacpp_base_url, model, name="llamacpp", timeout=t)
    return None
