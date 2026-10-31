"""Local, free embeddings via fastembed (ONNX). See docs/adr/0003-fastembed-embeddings.md."""

from typing import Any


class FastEmbedProvider:
    name = "fastembed"
    dimensions = 384

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model_name = model_name
        self._model: Any = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name)
        return [[float(x) for x in v] for v in self._model.embed(texts)]
