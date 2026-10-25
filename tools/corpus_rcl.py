"""Share frozen RCL stage cases and text-only cache adapters across corpus tools."""

import datetime as dt
import gzip
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from lexinform.models import Bill, BillStatus, RclProject, TextDocument, process_summary
from lexinform.services.sources import RclTextSource
from tools.corpus_runtime import CorpusReads


@dataclass(frozen=True)
class RclCase:
    identity: str
    path: Path
    document: TextDocument | None


def projects(root: Path) -> Iterator[tuple[Path, RclProject]]:
    for path in sorted((root / "corpus").glob("1*.json")):
        yield path, RclProject.model_validate_json(path.read_bytes())


def stage_cases(path: Path, project: RclProject, *, as_of: dt.datetime) -> Iterator[RclCase]:
    source = RclTextSource()
    for stage in project.reached_stages:
        snapshot = project.model_copy(
            update={"stages": tuple(s for s in project.stages if s.number <= stage.number)}
        )
        bill = Bill(
            summary=process_summary(snapshot, term=10),
            rcl=snapshot,
            status=BillStatus.ANALYSIS_PENDING,
            first_seen_at=as_of,
            last_checked_at=as_of,
        )
        yield RclCase(f"{project.id}/{stage.number}", path, source.locate(bill).document)


class CachedExtractor:
    def __init__(self, corpus: Path) -> None:
        self.corpus = corpus

    def extract(self, data: bytes) -> str:
        path = self.corpus / f"documents/text/{data.decode()}.txt.gz"
        return gzip.decompress(path.read_bytes()).decode() if path.exists() else ""

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        raise AssertionError("Cached text replay cannot validate scanned pages")


class RequiredCachedExtractor(CachedExtractor):
    def __init__(self, corpus: Path, reads: CorpusReads) -> None:
        super().__init__(corpus)
        self.reads = reads

    def extract(self, data: bytes) -> str:
        self.reads.require(self.corpus / f"documents/text/{data.decode()}.txt.gz")
        return super().extract(data)


def document_id(url: str, *, max_bytes: int | None = None) -> bytes:
    match = re.search(r"dokument(\d+)", url)
    if match is None:
        raise ValueError("URL absent from the RCL document corpus")
    return match[1].encode()
