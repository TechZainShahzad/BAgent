from app.config import Settings
from app.providers.embeddings.base import EmbeddingProvider, FakeEmbeddingProvider


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_provider == "fastembed":
        from app.providers.embeddings.fastembed_provider import FastEmbedProvider

        return FastEmbedProvider()
    raise NotImplementedError(f"embedding provider {settings.embedding_provider!r}")
