"""Single registry of durable job handlers, shared by the worker CLI and tests."""

from typing import Any

from app.config import Settings
from app.orchestration.jobs import Handler
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.services.analysis import make_analyze_handler
from app.services.autopilot import AUTOPILOT_JOB, make_autopilot_handler
from app.services.delivery import EXECUTE_JOB, make_execute_handler
from app.services.interview import WARM_JOB, make_plan_handler, make_warm_handler
from app.services.live import PLAN_JOB, WINDOW_JOB, make_window_handler
from app.services.meetings import ANALYZE_JOB


def build_handlers(
    settings: Settings,
    provider: LLMProvider,
    embedder: EmbeddingProvider | None,
    executor_factory: Any = None,
) -> dict[str, Handler]:
    return {
        ANALYZE_JOB: make_analyze_handler(provider, settings, embedder),
        EXECUTE_JOB: make_execute_handler(settings, executor_factory),
        WINDOW_JOB: make_window_handler(provider, settings, embedder),
        PLAN_JOB: make_plan_handler(provider, settings),
        WARM_JOB: make_warm_handler(provider),
        AUTOPILOT_JOB: make_autopilot_handler(provider, settings, executor_factory),
    }
