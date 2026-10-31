"""LLM accountability (T5): every call is recorded with metadata and hashes, never content.

Written in its own short transaction so the record survives a rolled-back job transaction.
"""

import logging
import time
import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.persistence.models import LLMCallLog
from app.providers.llm.base import LLMProvider, LLMRequest, LLMResponse

log = logging.getLogger(__name__)


class LoggedLLMProvider:
    def __init__(self, inner: LLMProvider, factory: sessionmaker[Session]) -> None:
        self.inner = inner
        self.factory = factory
        self.name = inner.name

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        exit_class, response = "ok", None
        try:
            response = await self.inner.generate(request)
            return response
        except Exception as exc:
            exit_class = type(exc).__name__
            raise
        finally:
            self._record(request, response, exit_class, int((time.monotonic() - started) * 1000))

    def _record(
        self, request: LLMRequest, response: LLMResponse | None, exit_class: str, duration_ms: int
    ) -> None:
        try:
            try:
                client_uuid: uuid.UUID | None = uuid.UUID(request.client_id)
            except ValueError:
                client_uuid = None
            with self.factory() as s:
                s.add(
                    LLMCallLog(
                        client_id=client_uuid,
                        agent=request.agent,
                        template_version=request.template_version,
                        schema_version=request.schema_version,
                        provider=self.name,
                        model=response.model if response else None,
                        duration_ms=response.duration_ms
                        if response and response.duration_ms
                        else duration_ms,
                        exit_class=exit_class,
                        input_hash=request.input_hash(),
                        output_hash=response.output_hash() if response else None,
                        input_tokens=response.input_tokens if response else None,
                        output_tokens=response.output_tokens if response else None,
                        cost_usd=response.cost_usd if response else None,
                        correlation_id=request.correlation_id,
                    )
                )
                s.commit()
        except Exception:  # accounting must never break the call, but must not be silent either
            log.exception("failed to record LLM call log")
