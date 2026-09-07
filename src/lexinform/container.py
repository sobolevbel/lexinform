"""Composition root: builds adapters and services from Settings. No DI framework."""

from __future__ import annotations

from dataclasses import dataclass, field

import anthropic

from lexinform.adapters.console import ConsolePublisher, ConsoleRunNotifier
from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.pdf_text import PypdfTextExtractor, TextBudget
from lexinform.adapters.sejm_api import SejmApiClient
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram import TelegramBotClient, TelegramPublisher, TelegramRunNotifier
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.clock import SystemClock
from lexinform.keywords import KeywordPrefilter
from lexinform.ports import Publisher, RunNotifier
from lexinform.services.analysis import AnalysisService
from lexinform.services.discovery import BillDiscoveryService
from lexinform.services.documents import PdfTextLoader
from lexinform.services.pipeline import DailyPipeline
from lexinform.services.publishing import PublishingService
from lexinform.services.text_prefilter import TextPrefilterService
from lexinform.services.tracking import StatusTrackingService
from lexinform.settings import Settings


@dataclass
class Container:
    settings: Settings
    clock: SystemClock
    repo: SqliteBillRepository
    gateway: SejmApiClient
    formatter: MessageFormatter
    prefilter: KeywordPrefilter
    _telegram: TelegramBotClient | None = field(default=None, init=False, repr=False)
    _loader: PdfTextLoader | None = field(default=None, init=False, repr=False)

    def pdf_loader(self) -> PdfTextLoader:
        """One loader (and text cache) per process, shared by the text prefilter and analysis."""
        if self._loader is None:
            self._loader = PdfTextLoader(
                self.gateway,
                PypdfTextExtractor(),
                max_bytes=self.settings.max_pdf_download_mb * 1024 * 1024,
            )
        return self._loader

    def analyzer(self) -> AnthropicAnalyzer:
        return AnthropicAnalyzer(
            anthropic.Anthropic(api_key=self.settings.anthropic_api_key),
            model=self.settings.llm_model,
            output_language=self.settings.output_language,
            effort=self.settings.llm_effort,
            max_tokens=self.settings.llm_max_tokens,
            clock=self.clock.now,
        )

    def analysis_service(self) -> AnalysisService:
        return AnalysisService(
            self.gateway,
            self.repo,
            self.pdf_loader(),
            self.analyzer(),
            text_budget=TextBudget(self.settings.text_budget_chars),
            max_attempts=self.settings.max_analysis_attempts,
        )

    def discovery_service(self) -> BillDiscoveryService:
        return BillDiscoveryService(
            self.gateway,
            self.repo,
            self.prefilter,
            self.clock,
            text_prefilter=self.settings.text_prefilter_enabled,
        )

    def text_prefilter_service(self) -> TextPrefilterService | None:
        if not self.settings.text_prefilter_enabled:
            return None
        return TextPrefilterService(
            self.gateway,
            self.repo,
            self.pdf_loader(),
            self.prefilter,
            min_distinct=self.settings.text_prefilter_min_distinct,
            min_occurrences=self.settings.text_prefilter_min_occurrences,
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
        if dry_run:
            return ConsolePublisher(self.formatter)
        return self.telegram_publisher()

    def run_notifier(self, *, dry_run: bool) -> RunNotifier | None:
        if dry_run:
            return ConsoleRunNotifier(self.formatter)
        if not self.settings.telegram_log_channel_id:
            return None
        return TelegramRunNotifier(
            self.telegram_client(), self.formatter, channel_id=self.settings.telegram_log_channel_id
        )

    def channel_id(self) -> str:
        return self.settings.telegram_channel_id or "console"

    def pipeline(self, *, dry_run: bool) -> DailyPipeline:
        publisher = self.publisher(dry_run=dry_run)
        channel = self.channel_id()
        analysis = self.analysis_service()
        return DailyPipeline(
            self.repo,
            self.discovery_service(),
            analysis,
            PublishingService(
                self.gateway,
                self.repo,
                publisher,
                self.clock,
                channel_id=channel,
                max_attempts=self.settings.max_publish_attempts,
            ),
            StatusTrackingService(
                self.gateway,
                self.repo,
                publisher,
                self.clock,
                channel_id=channel,
                analysis=analysis,
                closed_grace_days=self.settings.track_closed_grace_days,
                max_publish_attempts=self.settings.max_publish_attempts,
                club_breakdown=self.settings.voting_club_breakdown,
            ),
            self.clock,
            notifier=self.run_notifier(dry_run=dry_run),
            text_prefilter=self.text_prefilter_service(),
            first_run_lookback_days=self.settings.first_run_lookback_days,
            rerun_overlap_days=self.settings.rerun_overlap_days,
            pre_print=self.settings.pre_print_enabled,
        )

    def close(self) -> None:
        self.gateway.close()
        self.repo.close()
        if self._telegram is not None:
            self._telegram.close()


def build_container(settings: Settings) -> Container:
    repo = SqliteBillRepository(settings.db_path)
    repo.migrate()
    gateway = SejmApiClient(
        settings.sejm_api_base_url,
        page_size=settings.sejm_page_size,
        timeout=settings.sejm_timeout_seconds,
    )
    return Container(
        settings=settings,
        clock=SystemClock(),
        repo=repo,
        gateway=gateway,
        formatter=MessageFormatter(
            settings.output_language, api_base_url=settings.sejm_api_base_url
        ),
        prefilter=KeywordPrefilter(),
    )
