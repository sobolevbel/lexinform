"""Runtime configuration. Environment variables with the LEXINFORM_ prefix, or a .env file."""

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
    sejm_concurrency: int = Field(default=4, ge=1)  # parallel PDF downloads / process lookups

    # RCL (legislacja.rcl.gov.pl): government projects before they reach the Sejm
    rcl_enabled: bool = True
    rcl_base_url: str = "https://legislacja.rcl.gov.pl"
    rcl_timeout_seconds: float = 60.0  # a project page takes ~10 s to render
    rcl_concurrency: int = Field(default=6, ge=1)  # projects read at once (pages are slow)

    # Storage
    db_path: Path = Path("lexinform.db")

    # LLM. The key is read under its plain name (no LEXINFORM_ prefix) from the environment or
    # .env so that one variable serves both this app and the anthropic SDK.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    llm_model: str = "claude-opus-5"
    llm_effort: Effort = "medium"
    llm_max_tokens: int = 4000
    llm_concurrency: int = Field(default=2, ge=1)  # bills analysed at the same time
    # Cheap first pass on excerpts before the full analysis of long prints; "" disables it.
    llm_triage_model: str = "claude-sonnet-5"
    triage_min_chars: int = 20_000  # shorter texts go straight to the full analysis
    triage_min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
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
    min_score: int = Field(default=3, ge=1, le=5)
    max_publish_per_run: int = 10
    max_analyze_per_run: int = 40
    max_analysis_attempts: int = 3
    text_prefilter_enabled: bool = True  # scan the print PDF when the title says nothing
    text_prefilter_min_distinct: int = 2  # accept when this many different patterns occur ...
    text_prefilter_min_occurrences: int = 3  # ... or when patterns occur this many times in total
    text_prefilter_max_per_run: int = 20
    pre_print_enabled: bool = True  # also watch /bills for bills without a print number yet
    voting_club_breakdown: bool = True  # fetch per-MP votes to show how each club voted
    max_publish_attempts: int = 3  # failed posts are retried on later runs up to this many times
    first_run_lookback_days: int = 1
    rerun_overlap_days: int = 1
    track_closed_grace_days: int = 90  # Dz.U. publication follows ~30-40 days after closure
    track_passed_max_days: int = 180  # follow passed bills without a published act this long
    track_full_weekday: int = Field(default=0, ge=0, le=6)  # weekday of the full check (0 = Monday)
    in_force_reminders: bool = True  # post a reminder on the day the act enters into force
    consultation_reminders: bool = True  # remind before a public consultation closes ...
    consultation_reminder_days: int = Field(default=3, ge=0)  # ... this many days ahead
    agenda_watch: bool = (
        True  # post when a followed bill appears on a committee/Sejm sitting agenda
    )

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
