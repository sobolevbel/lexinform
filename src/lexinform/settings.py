"""Runtime configuration. Environment variables with the LEXINFORM_ prefix, or a .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEXINFORM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Sejm
    term: int = 10
    sejm_api_base_url: str = "https://api.sejm.gov.pl"
    sejm_page_size: int = 100
    sejm_timeout_seconds: float = 30.0

    # Storage
    db_path: Path = Path("lexinform.db")

    # LLM. The key is read under its plain name (no LEXINFORM_ prefix) from the environment or
    # .env so that one variable serves both this app and the anthropic SDK.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    llm_model: str = "claude-opus-5"
    llm_effort: Effort = "medium"
    llm_max_tokens: int = 4000
    output_language: str = "ru"
    # Safety cap only: Polish text is ~2 chars/token, so this is ~750k tokens and fits the
    # 1M context of the default model. Real prints (even 800k-char ones) go in whole.
    text_budget_chars: int = 1_500_000
    max_pdf_download_mb: int = 25

    # Telegram
    telegram_bot_token: str = ""
    telegram_channel_id: str = ""
    telegram_log_channel_id: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"

    # Pipeline
    min_score: int = Field(default=2, ge=1, le=5)
    max_publish_per_run: int = 10
    max_analyze_per_run: int = 40
    max_analysis_attempts: int = 3
    voting_club_breakdown: bool = True  # fetch per-MP votes to show how each club voted
    max_publish_attempts: int = 3  # failed posts are retried on later runs up to this many times
    first_run_lookback_days: int = 1
    rerun_overlap_days: int = 1
    track_closed_grace_days: int = 90  # Dz.U. publication follows ~30-40 days after closure

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    def require_telegram(self) -> None:
        missing = [
            name
            for name, value in (
                ("LEXINFORM_TELEGRAM_BOT_TOKEN", self.telegram_bot_token),
                ("LEXINFORM_TELEGRAM_CHANNEL_ID", self.telegram_channel_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")
