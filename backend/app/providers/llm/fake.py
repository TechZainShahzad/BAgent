"""Deterministic fake LLM for tests and dry runs. No network, no CLI."""

from collections.abc import Callable
from typing import Any

from app.providers.llm.base import LLMError, LLMRequest, LLMResponse

Responder = Callable[[LLMRequest], dict[str, Any] | LLMError]


class FakeLLMProvider:
    name = "fake"

    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder or (lambda req: {})
        self.calls: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        result = self.responder(request)
        if isinstance(result, LLMError):
            raise result
        return LLMResponse(data=result, provider=self.name, model="fake", duration_ms=1)
