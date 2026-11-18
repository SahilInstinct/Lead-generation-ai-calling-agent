"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration settings for the ApkaPG backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(default="ApkaPG AI Outbound Calling System")
    app_env: Literal["development", "staging", "production", "test"] = Field(
        default="development"
    )
    debug: bool = Field(default=False)
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8001, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )

    # Security
    api_auth_key: str = Field(default="")

    # Local Storage
    data_dir: Path = Field(default=Path("data"))

    # Testing / Provider Mode
    mock_providers: bool = Field(default=True)

    # Dograh AI
    dograh_api_base_url: str = Field(default="http://127.0.0.1:8000")
    dograh_api_key: str = Field(default="")
    dograh_workflow_uuid: str = Field(default="")
    dograh_workflow_id: int | None = Field(default=None)
    dograh_use_draft_test: bool = Field(default=False)
    dograh_telephony_config_id: int | None = Field(default=None)
    dograh_webhook_secret: str = Field(default="")

    # Vobiz (managed inside Dograh Telephony by default)
    vobiz_mode: Literal["dograh_managed", "direct"] = Field(default="dograh_managed")
    vobiz_auth_id: str = Field(default="")
    vobiz_caller_id: str = Field(default="")
    vobiz_webhook_secret: str = Field(default="")

    # LLM & TTS reference keys (if needed outside Dograh)
    groq_api_key: str = Field(default="")
    sarvam_api_key: str = Field(default="")

    @field_validator("dograh_workflow_id", "dograh_telephony_config_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, value):
        if value == "" or value is None:
            return None
        return value

    @property
    def leads_dir(self) -> Path:
        return self.data_dir / "leads"

    @property
    def calls_dir(self) -> Path:
        return self.data_dir / "calls"

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def calls_history_file(self) -> Path:
        return self.calls_dir / "call_history.json"

    @property
    def leads_json_file(self) -> Path:
        return self.leads_dir / "leads.json"

    @property
    def leads_csv_file(self) -> Path:
        return self.leads_dir / "leads.csv"

    @property
    def transcripts_json_file(self) -> Path:
        return self.transcripts_dir / "call_transcripts.json"

    def ensure_data_dirs(self) -> None:
        """Create required local storage directories if they do not exist."""
        for directory in (
            self.data_dir,
            self.leads_dir,
            self.calls_dir,
            self.transcripts_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def get_secret_values(self) -> list[str]:
        """Return non-empty secret values for log redaction."""
        candidates = [
            self.api_auth_key,
            self.vobiz_webhook_secret,
            self.dograh_api_key,
            self.dograh_webhook_secret,
            self.groq_api_key,
            self.sarvam_api_key,
        ]
        return [value for value in candidates if value and len(value) >= 4]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()