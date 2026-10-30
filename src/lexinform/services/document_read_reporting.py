"""Standalone callers own loader observations without measuring execution or model accounting."""

import datetime as dt
import logging
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass

from lexinform.log_context import log_context
from lexinform.models import CliReadOperation, RunMode, RunReport
from lexinform.ports import (
    BillRepository,
    Clock,
    DocumentArtifactObserver,
    DocumentDownloadMonitor,
    DocumentFileReadObserver,
    DocumentReadMonitor,
    SourceHistoryPersistence,
)
from lexinform.services.document_history import (
    persist_source_history,
    record_artifact_reads,
    record_artifacts,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CliDocumentReadReporter:
    repo: BillRepository
    clock: Clock
    reads: DocumentReadMonitor
    downloads: DocumentDownloadMonitor
    artifacts: DocumentArtifactObserver
    file_reads: DocumentFileReadObserver
    retention: dt.timedelta
    source_history: SourceHistoryPersistence | None = None

    @contextmanager
    def capture(self, operation: CliReadOperation) -> Iterator[None]:
        self.reads.reset_read_metrics()
        self.downloads.reset_download_diagnostics()
        started = self.clock.now()
        report = RunReport(
            started_at=started,
            since=started,
            mode=RunMode.CLI_READS,
            cli_read_operation=operation,
        )
        run_id = self.repo.start_run(report)
        with log_context(run_id=run_id):
            try:
                yield
            finally:
                self._finish(run_id, report)

    def _finish(self, run_id: int, report: RunReport) -> None:
        try:
            finished = self.clock.now()
            report.finished_at = finished
            report.document_reads = self.reads.read_metrics()
            report.document_download_diagnostics = self.downloads.read_download_diagnostics()
            report.metadata_url_omissions = self.downloads.read_metadata_url_omissions()
            artifacts = self.artifacts.read_artifacts()
            reads = self.file_reads.read_artifact_reads()
            with self.repo.atomic():
                record_artifacts(self.repo, artifacts, recorded_at=finished)
                for extraction in self.artifacts.read_extraction_results():
                    self.repo.save_extraction_result(extraction, recorded_at=finished)
                record_artifact_reads(self.repo, reads, recorded_at=finished)
                if self.source_history is not None:
                    persist_source_history(self.repo, self.source_history, recorded_at=finished)
                self.repo.finish_run(run_id, report)
                self.repo.prune_runs(before=finished - self.retention)
        except BaseException as exc:
            with suppress(BaseException):
                log.warning("CLI document-read capture failed (%s)", type(exc).__name__[:128])
