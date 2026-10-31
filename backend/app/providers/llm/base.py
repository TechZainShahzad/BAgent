"""LLM port, typed errors and validated-generation helper."""

import hashlib
import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base for all provider failures. Always surfaced, never swallowed."""


class CliMissingError(LLMError): ...


class NotAuthenticatedError(LLMError): ...


class UsageLimitError(LLMError): ...


class ModelUnavailableError(LLMError): ...


class NonZeroExitError(LLMError): ...


class LLMTimeoutError(LLMError): ...


class InvalidJsonError(LLMError): ...


class SchemaMismatchError(LLMError): ...


class CancelledByCallerError(LLMError): ...


class LLMRequest(BaseModel):
    agent: str
    system_prompt: str
    user_prompt: str  # untrusted content must already be delimited by the caller
    output_schema: dict[str, Any]
    client_id: str
    correlation_id: str = "-"
    template_version: str = "v1"
    schema_version: str = "v1"
    # Latency-sensitive callers (the live interviewer) may reuse one warm provider process for the
    # same key. The key must be scoped to one client AND one meeting; never share across clients.
    warm_key: str | None = None

    def input_hash_static(self) -> str:
        """Hash of the parts that are fixed for a warm process (system prompt and schema)."""
        blob = json.dumps([self.system_prompt, self.output_schema], sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def input_hash(self) -> str:
        blob = json.dumps(
            [self.system_prompt, self.user_prompt, self.output_schema], sort_keys=True
        ).encode()
        return hashlib.sha256(blob).hexdigest()


class LLMResponse(BaseModel):
    data: dict[str, Any]
    provider: str
    model: str | None = None
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    def output_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.data, sort_keys=True).encode()).hexdigest()


class LLMProvider(Protocol):
    name: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...


async def generate_validated[T: BaseModel](
    provider: LLMProvider, request: LLMRequest, model: type[T]
) -> tuple[T, LLMResponse]:
    """Call the provider and validate against the Pydantic schema. Invalid output is a failure."""
    response = await provider.generate(request)
    try:
        return model.model_validate(response.data), response
    except ValidationError as exc:
        raise SchemaMismatchError(
            f"{request.agent}: {exc.error_count()} validation errors"
        ) from exc


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
