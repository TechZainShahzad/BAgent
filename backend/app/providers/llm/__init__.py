from app.config import Settings
from app.providers.llm.base import LLMProvider
from app.providers.llm.claude_cli import ClaudeCliProvider
from app.providers.llm.fake import FakeLLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLMProvider()
    if settings.llm_provider == "claude_cli":
        if "claude_cli" not in {p.strip() for p in settings.approved_providers.split(",")}:
            raise PermissionError("claude_cli is not in APPROVED_PROVIDERS")
        return ClaudeCliProvider(
            model=settings.claude_model,
            timeout_s=settings.claude_timeout_s,
            max_concurrency=settings.claude_max_concurrency,
            log_content=settings.log_llm_content,
        )
    raise NotImplementedError(f"LLM provider {settings.llm_provider!r} not implemented yet")
