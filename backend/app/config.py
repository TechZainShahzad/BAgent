from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT_ENV, extra="ignore")

    database_url: str = "postgresql+psycopg://ba:ba_dev_password@127.0.0.1:5433/ba"
    test_database_url: str = "postgresql+psycopg://ba:ba_dev_password@127.0.0.1:5433/ba_test"

    autonomy_mode: str = Field(default="dry_run", pattern="^(dry_run|propose|autonomous)$")
    kill_switch: bool = False

    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: SecretStr = SecretStr("")
    jira_project_allowlist: str = ""

    llm_provider: str = Field(default="fake", pattern="^(fake|claude_cli|anthropic_api)$")
    claude_model: str = "sonnet"
    claude_timeout_s: float = 300.0
    claude_max_concurrency: int = 1
    log_llm_content: bool = False
    approved_providers: str = "claude_cli"

    transcript_grace_hours: float = 0.0
    evidence_retention_days: int = 365
    conversation_retention_days: int = 365  # 0 = do not keep the written conversation
    embedding_provider: str = Field(default="fake", pattern="^(fake|fastembed)$")

    session_secret: SecretStr = SecretStr("dev-only-change-me")
    session_cookie_secure: bool = False
    api_host: str = "127.0.0.1"
    dashboard_base_url: str = "http://127.0.0.1:5173"
    api_base_url: str = "http://127.0.0.1:8000"  # what the capture process calls

    @property
    def jira_allowlist(self) -> set[str]:
        return {p.strip() for p in self.jira_project_allowlist.split(",") if p.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
