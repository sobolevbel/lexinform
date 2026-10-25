"""Share frozen RCL stage cases and text-only cache adapters across corpus tools."""

import datetime as dt
import gzip
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lexinform.models import Bill, BillStatus, RclProject, TextDocument, process_summary
from lexinform.services.sources import RclTextSource
from tools.corpus_artifacts import digest, read_artifact
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


class DerivedTexts:
    def __init__(self, corpus: Path, report: Path, pin: str | None) -> None:
        raw = read_artifact(report)
        if digest(raw) != pin:
            raise ValueError("Derivative report SHA mismatch")
        payload = json.loads(raw)
        if (
            payload.get("status") != "recorded"
            or payload.get("changed_inputs")
            or payload.get("changed_code")
        ):
            raise ValueError("Derivative generation is not a completed immutable observation")
        self.corpus, self.base = corpus.resolve(), report.parent.resolve()
        self.rows: dict[str, Any] = payload["observations"]
        self.inputs = {str(report.resolve()): digest(raw)}

    def get(self, identity: str) -> str | None:
        row = self.rows.get(identity)
        if row is None or row["state"] not in ("recovered", "still_empty"):
            return None
        source = self.corpus / row["raw_path"]
        derived = self.base / row["extraction"]
        for path, base in ((source, self.corpus), (derived, self.base)):
            if path.resolve() != path or not path.resolve().is_relative_to(base):
                raise ValueError("Derivative or raw source path escapes its root")
        if digest(read_artifact(source)) != row["raw_sha256"]:
            raise ValueError("Derivative raw source changed")
        data = read_artifact(derived)
        text = gzip.decompress(data).decode()
        if digest(text.encode()) != row["text_sha256"]:
            raise ValueError("Derivative text changed")
        self.inputs[str(derived)] = digest(data)
        return text


class CachedExtractor:
    def __init__(
        self,
        corpus: Path,
        *,
        derivatives: Path | None = None,
        derivatives_sha256: str | None = None,
    ) -> None:
        self.corpus = corpus
        self.derived = (
            DerivedTexts(corpus, derivatives, derivatives_sha256) if derivatives else None
        )

    def extract(self, data: bytes) -> str:
        if self.derived is not None:
            text = self.derived.get(data.decode())
            if text is not None:
                return text
        path = self.corpus / f"documents/text/{data.decode()}.txt.gz"
        return gzip.decompress(path.read_bytes()).decode() if path.exists() else ""

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        raise AssertionError("Cached text replay cannot validate scanned pages")


class RequiredCachedExtractor(CachedExtractor):
    def __init__(
        self,
        corpus: Path,
        reads: CorpusReads,
        *,
        derivatives: Path | None = None,
        derivatives_sha256: str | None = None,
    ) -> None:
        super().__init__(corpus, derivatives=derivatives, derivatives_sha256=derivatives_sha256)
        self.reads = reads
        if self.derived is not None:
            self.reads.allowed += (self.derived.base,)

    def extract(self, data: bytes) -> str:
        if self.derived is None or self.derived.rows.get(data.decode(), {}).get("state") not in (
            "recovered",
            "still_empty",
        ):
            self.reads.require(self.corpus / f"documents/text/{data.decode()}.txt.gz")
        return super().extract(data)


def document_id(url: str, *, max_bytes: int | None = None) -> bytes:
    match = re.search(r"dokument(\d+)", url)
    if match is None:
        raise ValueError("URL absent from the RCL document corpus")
    return match[1].encode()
