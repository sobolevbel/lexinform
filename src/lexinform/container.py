"""Manual wiring of adapters and services; the CLI and the tests build a `Container` and ask it
for the pipeline or for single services. Every collaborator is typed on its port, so the test
harness builds the same container from fakes and the wiring the daily run uses is what the
tests exercise."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import anthropic

from lexinform.adapters.console import ConsolePublisher, ConsoleReplier, ConsoleRunNotifier
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.github_inbox import GitHubInboxWriter
from lexinform.adapters.inbox_files import FileInbox
from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.rcl_html import RclClient
from lexinform.adapters.sejm_api import SejmApiClient
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram import (
    TelegramAcknowledger,
    TelegramBotClient,
    TelegramOperatorReplier,
    TelegramPublisher,
    TelegramRunNotifier,
)
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.clock import SystemClock
from lexinform.keywords import KeywordPrefilter
from lexinform.ports import (
    BillRepository,
    Clock,
    CommandInbox,
    Downloader,
    LlmAnalyzer,
    OperatorReplier,
    Publisher,
    RclGateway,
    RunNotifier,
    SejmApi,
    TextExtractor,
)
from lexinform.pricing import price_of
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisService
from lexinform.services.commands import CommandService
from lexinform.services.discovery import BillDiscoveryService
from lexinform.services.documents import TextLoader
from lexinform.services.listener import CommandListener
from lexinform.services.lookup import BillLookup
from lexinform.services.pipeline import DailyPipeline
from lexinform.services.publishing import PublishingService
from lexinform.services.rcl_discovery import RclDiscoveryService
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.signatories import SejmAuthorsResolver
from lexinform.services.sources import RclTextSource, SejmTextSource, TextSources
from lexinform.services.terms import TermResolver
from lexinform.services.text_prefilter import TextPrefilterService
from lexinform.services.tracking import StatusTrackingService
from lexinform.settings import Settings

LOCAL_TZ = ZoneInfo("Europe/Warsaw")  # the readers' and the Sejm's day, whatever the runner's zone


@dataclass
class Container:
    """The wiring. Services are built once and shared (`discovery_service()` and the pipeline's
    discovery are the same object), so a test can hold a service and run the pipeline."""

    settings: Settings
    clock: Clock
    repo: BillRepository
    gateway: SejmApi
    formatter: MessageFormatter
    prefilter: KeywordPrefilter
    terms: TermResolver
    rcl: RclGateway | None = None  # None when LEXINFORM_RCL_ENABLED is off
    # Collaborators a test (or a dry run) supplies instead of the real adapters.
    llm: LlmAnalyzer | None = None
    extractor: TextExtractor | None = None
    publisher_override: Publisher | None = None
    notifier_override: RunNotifier | None = None
    inbox_override: CommandInbox | None = None
    replier_override: OperatorReplier | None = None
    _telegram: TelegramBotClient | None = field(default=None, init=False, repr=False)
    _loader: TextLoader | None = field(default=None, init=False, repr=False)
    _services: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    def term(self) -> int:
        """The Sejm term operator commands work in: `LEXINFORM_TERM` when set, else the current
        one as the API reports it (the newest one in the database when the API is down)."""
        return self.terms.current()

    def bill_lookup(self) -> BillLookup:
        """One bill by number or reference, fetched and prefiltered on first sight."""
        return self._once(
            "lookup",
            lambda: BillLookup(
                self.gateway,
                self.repo,
                self.prefilter,
                self.clock,
                self.terms,
                rcl_reader=self.rcl_reader() if self.rcl is not None else None,
                projects=self.rcl,
                text_prefilter=self.settings.text_prefilter_enabled,
            ),
        )

    def _once[T](self, key: str, factory: Callable[[], T]) -> T:
        """One instance per service (and per dry-run flag where it matters)."""
        if key not in self._services:
            self._services[key] = factory()
        return cast(T, self._services[key])

    def text_loader(self) -> TextLoader:
        """One loader (and text cache) per process, shared by the text prefilter and analysis.
        Downloads are routed by host to the client of that system."""
        if self._loader is None:
            downloaders: dict[str, Downloader] = {
                _host(self.settings.sejm_api_base_url): self.gateway.download
            }
            if self.rcl is not None:
                downloaders[_host(self.settings.rcl_base_url)] = self.rcl.download
            max_bytes = self.settings.max_pdf_download_mb * 1024 * 1024
            extractor = self.extractor or DocumentTextExtractor(
                PypdfTextExtractor(),
                DocxTextExtractor(),
                DocTextExtractor(),
                max_member_bytes=max_bytes,
            )
            self._loader = TextLoader(downloaders, extractor, max_bytes=max_bytes)
        return self._loader

    def text_sources(self) -> TextSources:
        rcl = RclTextSource() if self.rcl is not None else None
        return TextSources(SejmTextSource(self.gateway), rcl=rcl)

    def rcl_reader(self) -> RclProjectReader:
        if self.rcl is None:
            raise RuntimeError("RCL is disabled (LEXINFORM_RCL_ENABLED=false)")
        return RclProjectReader(self.rcl, self.text_loader())

    def rcl_discovery_service(self) -> RclDiscoveryService | None:
        if self.rcl is None:
            return None
        rcl = self.rcl
        return self._once(
            "rcl_discovery",
            lambda: RclDiscoveryService(
                rcl,
                self.repo,
                self.rcl_reader(),
                self.prefilter,
                self.clock,
                text_prefilter=self.settings.text_prefilter_enabled,
                workers=self.settings.rcl_concurrency,
            ),
        )

    def analyzer(self) -> LlmAnalyzer:
        if self.llm is not None:
            return self.llm
        return AnthropicAnalyzer(
            anthropic.Anthropic(api_key=self.settings.anthropic_api_key),
            model=self.settings.llm_model,
            triage_model=self.settings.llm_triage_model or None,
            output_language=self.settings.output_language,
            effort=self.settings.llm_effort,
            max_tokens=self.settings.llm_max_tokens,
            clock=self.clock.now,
        )

    def analysis_service(self) -> AnalysisService:
        return self._once("analysis", self._build_analysis_service)

    def _build_analysis_service(self) -> AnalysisService:
        price = price_of(self.settings.llm_model)  # None: unknown model, no cost estimates
        return AnalysisService(
            self.repo,
            self.text_sources(),
            self.text_loader(),
            self.analyzer(),
            self.clock,
            text_budget=TextBudget(self.settings.text_budget_chars),
            authors=SejmAuthorsResolver(self.gateway),
            max_attempts=self.settings.max_analysis_attempts,
            workers=self.settings.llm_concurrency,
            input_price_usd_per_mtok=price[0] if price is not None else None,
            max_bill_cost_usd=self.settings.max_analysis_cost_usd,
            max_run_cost_usd=self.settings.max_run_cost_usd,
            triage=self.prefilter if self.settings.llm_triage_model else None,
            triage_min_chars=self.settings.triage_min_chars,
            triage_min_confidence=self.settings.triage_min_confidence,
        )

    def discovery_service(self) -> BillDiscoveryService:
        return self._once(
            "discovery",
            lambda: BillDiscoveryService(
                self.gateway,
                self.repo,
                self.prefilter,
                self.clock,
                text_prefilter=self.settings.text_prefilter_enabled,
                projects=self.rcl,
            ),
        )

    def text_prefilter_service(self) -> TextPrefilterService | None:
        if not self.settings.text_prefilter_enabled:
            return None
        return self._once(
            "text_prefilter",
            lambda: TextPrefilterService(
                self.repo,
                self.text_sources(),
                self.text_loader(),
                self.prefilter,
                min_distinct=self.settings.text_prefilter_min_distinct,
                min_occurrences=self.settings.text_prefilter_min_occurrences,
                workers=self.settings.sejm_concurrency,
            ),
        )

    def telegram_client(self) -> TelegramBotClient:
        if self._telegram is None:
            self.settings.require_telegram()
            self._telegram = TelegramBotClient(
                self.settings.telegram_bot_token, base_url=self.settings.telegram_api_base_url
            )
        return self._telegram

    def telegram_publisher(self, *, channel_id: str | None = None) -> TelegramPublisher:
        return TelegramPublisher(
            self.telegram_client(),
            self.formatter,
            channel_id=channel_id or self.settings.telegram_channel_id,
        )

    def publisher(self, *, dry_run: bool) -> Publisher:
        if self.publisher_override is not None:
            return self.publisher_override
        if dry_run:
            return self._once("console_publisher", lambda: ConsolePublisher(self.formatter))
        return self._once("telegram_publisher", self.telegram_publisher)

    def run_notifier(self, *, dry_run: bool) -> RunNotifier | None:
        if self.notifier_override is not None:
            return self.notifier_override
        if dry_run:
            return ConsoleRunNotifier(self.formatter)
        if not self.settings.telegram_log_channel_id:
            return None
        return TelegramRunNotifier(
            self.telegram_client(), self.formatter, channel_id=self.settings.telegram_log_channel_id
        )

    def channel_id(self) -> str:
        return self.settings.telegram_channel_id or "console"

    def command_inbox(self) -> CommandInbox | None:
        if self.inbox_override is not None:
            return self.inbox_override
        if self.settings.inbox_dir is None:
            return None
        return FileInbox(self.settings.inbox_dir)

    def operator_replier(self, *, dry_run: bool) -> OperatorReplier:
        if self.replier_override is not None:
            return self.replier_override
        if dry_run or not self.settings.telegram_log_channel_id:
            return ConsoleReplier(self.formatter)
        return TelegramOperatorReplier(
            self.telegram_client(), self.formatter, channel_id=self.settings.telegram_log_channel_id
        )

    def command_listener(self, *, dry_run: bool) -> CommandListener:
        """The relay: Telegram in, the inbox branch out. A dry run reads and reports only."""
        s = self.settings
        if not s.telegram_log_channel_id:
            raise ValueError("Missing required settings: LEXINFORM_TELEGRAM_LOG_CHANNEL_ID")
        client = self.telegram_client()
        writer = None
        acknowledger = None
        if not dry_run:
            writer = GitHubInboxWriter(s.github_repo, s.github_token, branch=s.inbox_branch)
            acknowledger = TelegramAcknowledger(client, channel_id=s.telegram_log_channel_id)
        return CommandListener(
            client,
            writer,
            acknowledger,
            channel_id=s.telegram_log_channel_id,
            poll_timeout=s.listen_timeout_seconds,
        )

    def command_service(self, *, dry_run: bool) -> CommandService | None:
        inbox = self.command_inbox()
        if inbox is None:
            return None
        return self._once(
            f"commands:{dry_run}",
            lambda: CommandService(
                self.repo,
                inbox,
                self.operator_replier(dry_run=dry_run),
                self.bill_lookup(),
                self.analysis_service(),
                self.publishing_service(dry_run=dry_run),
                self.clock,
                channel_id=self.channel_id(),
                text_prefilter=self.text_prefilter_service(),
            ),
        )

    def publishing_service(self, *, dry_run: bool) -> PublishingService:
        return self._once(
            f"publishing:{dry_run}",
            lambda: PublishingService(
                self.gateway,
                self.repo,
                self.publisher(dry_run=dry_run),
                self.clock,
                channel_id=self.channel_id(),
                max_attempts=self.settings.max_publish_attempts,
            ),
        )

    def tracking_service(self, *, dry_run: bool) -> StatusTrackingService:
        return self._once(
            f"tracking:{dry_run}",
            lambda: StatusTrackingService(
                self.gateway,
                self.repo,
                self.publisher(dry_run=dry_run),
                self.clock,
                channel_id=self.channel_id(),
                analysis=self.analysis_service(),
                eli=self.gateway,
                closed_grace_days=self.settings.track_closed_grace_days,
                passed_max_days=self.settings.track_passed_max_days,
                in_force_reminders=self.settings.in_force_reminders,
                consultation_reminder_days=(
                    self.settings.consultation_reminder_days
                    if self.settings.consultation_reminders
                    else None
                ),
                agenda_watch=self.settings.agenda_watch,
                rcl_reader=self.rcl_reader() if self.rcl is not None else None,
                max_publish_attempts=self.settings.max_publish_attempts,
                club_breakdown=self.settings.voting_club_breakdown,
                local_tz=LOCAL_TZ,
                text_prefilter=self.settings.text_prefilter_enabled,
                workers=self.settings.sejm_concurrency,
            ),
        )

    def pipeline(self, *, dry_run: bool) -> DailyPipeline:
        return self._once(
            f"pipeline:{dry_run}",
            lambda: DailyPipeline(
                self.repo,
                self.discovery_service(),
                self.analysis_service(),
                self.publishing_service(dry_run=dry_run),
                self.tracking_service(dry_run=dry_run),
                self.clock,
                terms=self.terms,
                notifier=self.run_notifier(dry_run=dry_run),
                text_prefilter=self.text_prefilter_service(),
                rcl_discovery=self.rcl_discovery_service(),
                commands=self.command_service(dry_run=dry_run),
                first_run_lookback_days=self.settings.first_run_lookback_days,
                rerun_overlap_days=self.settings.rerun_overlap_days,
                runs_retention_days=self.settings.runs_retention_days,
                pre_print=self.settings.pre_print_enabled,
                full_track_weekday=self.settings.track_full_weekday,
            ),
        )

    def close(self) -> None:
        self.gateway.close()
        if self.rcl is not None:
            self.rcl.close()
        self.repo.close()
        if self._telegram is not None:
            self._telegram.close()


def _host(url: str) -> str:
    return urlparse(url).hostname or ""


def build_container(settings: Settings) -> Container:
    repo = SqliteBillRepository(settings.db_path)
    repo.migrate()
    gateway = SejmApiClient(
        settings.sejm_api_base_url,
        page_size=settings.sejm_page_size,
        timeout=settings.sejm_timeout_seconds,
    )
    rcl = (
        RclClient(
            settings.rcl_base_url,
            timeout=settings.rcl_timeout_seconds,
            proxy=settings.rcl_proxy_url or None,
        )
        if settings.rcl_enabled
        else None
    )
    clock = SystemClock()
    return Container(
        settings=settings,
        clock=clock,
        repo=repo,
        gateway=gateway,
        formatter=MessageFormatter(
            settings.output_language, today=lambda: clock.now().astimezone(LOCAL_TZ).date()
        ),
        prefilter=KeywordPrefilter(),
        terms=TermResolver(gateway, repo, pinned=settings.term),
        rcl=rcl,
    )
