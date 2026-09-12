"""Runtime configuration. Environment variables with the LEXINFORM_ prefix, or a .env file.

Every field that needs more than its name to be understood carries a `description`, which is
what `.env.example` and the operator's documentation say about it.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEXINFORM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    term: int | None = Field(
        default=None,
        description="Pins the Sejm term. Empty takes the one the API flags as current, so the"
        " bot moves to a new kadencja by itself.",
    )
    sejm_api_base_url: str = "https://api.sejm.gov.pl"
    sejm_page_size: int = 100
    sejm_timeout_seconds: float = 30.0
    sejm_concurrency: int = Field(
        default=4, ge=1, description="Parallel PDF downloads and process lookups."
    )

    rcl_enabled: bool = Field(
        default=True, description="Follow government projects on legislacja.rcl.gov.pl."
    )
    rcl_base_url: str = "https://legislacja.rcl.gov.pl"
    rcl_timeout_seconds: float = Field(
        default=60.0, description="A project page takes some ten seconds to render."
    )
    rcl_concurrency: int = Field(
        default=6, ge=1, description="Projects read at once; the pages are slow."
    )
    rcl_proxy_url: str = Field(
        default="",
        description="RCL drops connections from outside the EU, GitHub runners included: an HTTP"
        " forward proxy with an EU address, `http://user:password@host:port`. Empty connects"
        " directly.",
    )

    wykaz_enabled: bool = Field(
        default=True,
        description="Follow the wykaz prac legislacyjnych RM: bills the government has only"
        " announced. One CSV of ~10 MB per run.",
    )
    wykaz_base_url: str = "https://www.gov.pl"
    wykaz_timeout_seconds: float = 60.0
    wykaz_proxy_url: str = Field(
        default="",
        description="gov.pl is a single Polish address, so it may one day need the same EU proxy"
        " as RCL; it answers GitHub runners today.",
    )

    db_path: Path = Path("lexinform.db")

    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Read under its plain name, without the LEXINFORM_ prefix, so that one"
        " variable serves both this app and the anthropic SDK.",
    )
    llm_model: str = "claude-opus-5"
    llm_effort: Effort = "medium"
    llm_max_tokens: int = 4000
    llm_concurrency: int = Field(default=2, ge=1, description="Bills analysed at the same time.")
    llm_triage_model: str = Field(
        default="claude-sonnet-5",
        description="Cheap first pass on excerpts before the full analysis of a long print;"
        " empty disables it.",
    )
    triage_min_chars: int = Field(
        default=20_000, description="Shorter texts go straight to the full analysis."
    )
    triage_min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    llm_map_model: str = Field(
        default="claude-haiku-4-5",
        description="Sorts the pages of a scan by what they hold, from small images of them, so"
        " that only the pages worth reading go to the analysis model. Empty falls back to the"
        " triage model.",
    )
    scan_map_min_pages: int = Field(
        default=12,
        ge=1,
        description="A scan shorter than this is read whole: mapping it is a request of its own"
        " and would cost more than the pages it saves.",
    )
    scan_map_page_width: int = Field(
        default=700,
        ge=200,
        description="Pixels across for the images the map is made from: a heading is legible at"
        " this size and the body is not, which is all the map needs (~900 tokens a page).",
    )
    max_analysis_cost_usd: float = Field(
        default=2.0,
        ge=0.0,
        description="A first analysis whose input alone is estimated above this is skipped"
        " (`skipped_cost`, revived with `lexinform reset`). 0 disables the guard.",
    )
    max_run_cost_usd: float = Field(
        default=15.0,
        ge=0.0,
        description="The analysis phase stops for the run once its model spend reaches this."
        " 0 disables the guard.",
    )
    output_language: str = "ru"
    text_budget_chars: int = Field(
        default=1_500_000,
        description="Safety cap only: Polish text is ~2 chars per token, so this is ~750k tokens"
        " and fits the 1M context of the default model. Real prints go in whole.",
    )
    max_pdf_download_mb: int = Field(
        default=200,
        description="Safety valve only, not a relevance rule: the body is buffered in memory and"
        " unpacked zip members are capped by the same number. Real prints reach 40 MB (druk 2865:"
        " 346 pages with a text layer), and a 25 MB cap silently skipped them.",
    )

    telegram_bot_token: str = ""
    telegram_channel_id: str = ""
    telegram_log_channel_id: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"

    inbox_dir: Path | None = Field(
        default=None,
        description="Where the operator commands wait: the relay files each as"
        " `{update_id}.json` into the git branch `inbox`, and a run reads the directory that"
        " branch is checked out in. Empty means no commands phase.",
    )

    @field_validator("inbox_dir", mode="before")
    @classmethod
    def _empty_inbox_dir_is_none(cls, value: object) -> object:
        """`LEXINFORM_INBOX_DIR=` (blank) means no inbox, not the current directory."""
        return None if isinstance(value, str) and not value.strip() else value

    github_repo: str = Field(
        default="", description="The relay's side: the inbox repository, as `owner/name`."
    )
    github_token: str = Field(
        default="",
        description="A fine-grained personal access token with Contents read/write on"
        " `github_repo`.",
    )
    inbox_branch: str = "inbox"
    listen_timeout_seconds: int = Field(
        default=50, ge=0, le=300, description="How long one getUpdates call waits for a post."
    )

    min_score: int = Field(default=3, ge=1, le=5)
    max_publish_per_run: int = 10
    max_analyze_per_run: int = 40
    max_analysis_attempts: int = 3
    text_prefilter_enabled: bool = Field(
        default=True, description="Scan the print PDF when the title says nothing."
    )
    text_prefilter_min_distinct: int = Field(
        default=2, description="Accept a text when this many different patterns occur in it."
    )
    text_prefilter_min_occurrences: int = Field(
        default=3, description="...or when the patterns occur this many times in total."
    )
    text_prefilter_max_per_run: int = 20
    pre_print_enabled: bool = Field(
        default=True, description="Also watch /bills for bills without a print number yet."
    )
    voting_club_breakdown: bool = Field(
        default=True, description="Fetch per-MP votes to show how each club voted."
    )
    max_publish_attempts: int = Field(
        default=3, description="Failed posts are retried on later runs up to this many times."
    )
    max_card_edits: int = Field(
        default=30,
        description="Cards re-rendered in place per run when what they say has drifted; bounds"
        " the run.",
    )
    first_run_lookback_days: int = 1
    rerun_overlap_days: int = 1
    runs_retention_days: int = Field(
        default=90, ge=7, description="Run records, with their reports, are kept this long."
    )
    track_closed_grace_days: int = Field(
        default=90,
        description="Publication in Dziennik Ustaw follows the Sejm's closure by 30 to 40 days.",
    )
    track_passed_max_days: int = Field(
        default=180, description="Follow passed bills without a published act this long."
    )
    track_pending_decision_max_days: int = Field(
        default=1095,
        description="A veto or a referral to the Tribunal can hold a law for years before an act"
        " appears.",
    )
    track_full_weekday: int = Field(
        default=0, ge=0, le=6, description="Weekday of the full check; 0 is Monday."
    )
    in_force_reminders: bool = Field(
        default=True, description="Post a reminder on the day the act enters into force."
    )
    consultation_reminders: bool = Field(
        default=True,
        description="Remind before a public consultation closes, and before applications to a"
        " public hearing close.",
    )
    consultation_reminder_days: int = Field(default=3, ge=0)
    decision_reminders: bool = Field(
        default=True,
        description="Remind before the Senate's 30 days (art. 121) and the President's 21"
        " (art. 122) run out.",
    )
    decision_reminder_days: int = Field(
        default=7,
        ge=0,
        description="Wider than the consultation's window on purpose: both dates are counted from"
        " the stage before the hand-over, so they fall a few days early, and the Senate's"
        " committee takes the act well before day 30.",
    )
    agenda_watch: bool = Field(
        default=True,
        description="Post when a followed bill appears on a committee or Sejm sitting agenda.",
    )

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
