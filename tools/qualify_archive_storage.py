"""Measure section-only fixture history growth from actual offline captured archive sources."""

import argparse
import datetime as dt
import gzip
import json
import logging
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import CandidateDispositionTrace, TextDocument
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.services.document_history import (
    DocumentSourceRead,
    SourceReadCapture,
    record_source_read,
)
from lexinform.services.documents import TextLoader
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.replay_archive_dispositions import INDEX_SHA256, OPTIONS, synthetic_inputs
from tools.source_history_doctor import audit

NOW = dt.datetime(2026, 11, 4, tzinfo=dt.UTC)


def ordinary_source(source: SourceReadCapture) -> SourceReadCapture:
    if not isinstance(source, DocumentSourceRead) or source.candidate_trace is None:
        return source
    value = source.candidate_trace.model_dump()
    value.pop("archive_members", None)
    trace = CandidateDispositionTrace.model_validate(value)
    return replace(source, candidate_trace=None if trace.is_routine_singleton else trace)


def measure_history(sources: Sequence[SourceReadCapture], directory: Path) -> dict[str, Any]:
    measurements: dict[str, Any] = {}
    for name, captured in (
        ("ordinary", [ordinary_source(item) for item in sources]),
        ("archive", sources),
    ):
        path = directory / f"{name}.db"
        with closing(SqliteBillRepository(path)) as repo:
            repo.migrate()
            for source in captured:
                with repo.atomic():
                    record_source_read(
                        repo,
                        source,
                        term=10,
                        number="archive-storage-fixture",
                        purpose="analysis",
                        recorded_at=NOW,
                    )
            first = repo.dump()
            for source in captured:
                with repo.atomic():
                    record_source_read(
                        repo,
                        source,
                        term=10,
                        number="archive-storage-fixture",
                        purpose="analysis",
                        recorded_at=NOW,
                    )
            if repo.dump() != first:
                raise ValueError("Identical captured fixture history did not deduplicate")
            repo.restore(first)
            if repo.dump() != first:
                raise ValueError("Fixture restore changed the public dump")
            size = len(first.encode())
            compressed = len(gzip.compress(first.encode(), mtime=0))
        before = digest(path.read_bytes())
        doctor = audit(path, include_candidate_traces=True)
        if doctor["status"] != "recorded" or digest(path.read_bytes()) != before:
            raise ValueError("Opt-in doctor rejected or changed fixture history")
        measurements[name] = {
            "sql_bytes": size,
            "gzip_bytes": compressed,
            "dump_sha256": digest(first.encode()),
            "deduplicated": True,
            "restore_exact": True,
            "doctor_counts": doctor["summary"],
        }
    measurements["delta"] = {
        key: measurements["archive"][key] - measurements["ordinary"][key]
        for key in ("sql_bytes", "gzip_bytes")
    }
    return measurements


def run(corpus: Path, cohort: Path, output: Path) -> dict[str, Any]:
    root, output = corpus.resolve(strict=True), output.resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError("Output must be new and outside the corpus")
    manifest = read_artifact(cohort)
    cases = json.loads(manifest)
    if cases["index_sha256_before"] != INDEX_SHA256 or cases["index_sha256_after"] != INDEX_SHA256:
        raise ValueError("Cohort INDEX differs from the frozen replay")
    reads, pins = CorpusReads(root), source_fingerprints()
    bodies: dict[str, bytes] = {}
    documents: list[TextDocument] = []
    captures: list[SourceReadCapture] = []
    counts: Counter[str] = Counter()
    trace_sizes: list[dict[str, int]] = []
    with reads.guard():
        if digest((root / "INDEX.json").read_bytes()) != INDEX_SHA256:
            raise ValueError("INDEX differs from the frozen replay")
        for case in cases["cases"]:
            raw = case["raw"]
            body = (root / raw["path"]).read_bytes()
            if digest(body) != raw["sha256"] or len(body) != raw["bytes"]:
                raise ValueError("Frozen cohort raw bytes differ")
            url = "https://replay.test/natural_" + str(case["id"])
            bodies[url] = body
            documents.append(TextDocument(url=url, kind="rcl"))
        if len(documents) >= 2:
            documents.append(
                documents[0].model_copy(
                    update={"compare_versions": True, "alternatives": (documents[1],)}
                )
            )
        synthetics = synthetic_inputs()
        for identity, body in synthetics.items():
            url = "https://replay.test/" + identity
            bodies[url] = body
            documents.append(TextDocument(url=url, kind="rcl"))

        def download(url: str, *, max_bytes: int | None = None) -> bytes:
            return bodies[url]

        loader = TextLoader(
            {"replay.test": download},
            DocumentTextExtractor(
                PypdfTextExtractor(),
                DocxTextExtractor(),
                DocTextExtractor(),
                max_member_bytes=OPTIONS["max_member_bytes"],
                max_part_chars=OPTIONS["max_part_chars"],
            ),
            max_bytes=OPTIONS["max_bytes"],
            cache_size=OPTIONS["cache_size"],
            physical_factory=PhysicalObservationCollector,
        )
        for document in documents:
            selected, loaded = loader.select_document(document)
            source = loader.capture_source_read(document, selected, loaded)
            captures.append(source)
            counts[type(source).__name__] += 1
            if isinstance(source, DocumentSourceRead) and source.candidate_trace is not None:
                counts["archive_traces"] += source.candidate_trace.archive_members is not None
                old = ordinary_source(source)
                old_trace = old.candidate_trace if isinstance(old, DocumentSourceRead) else None
                trace_sizes.append(
                    {
                        "ordinary": len(old_trace.model_dump_json(exclude_defaults=True).encode())
                        if old_trace is not None
                        else 0,
                        "archive": len(
                            source.candidate_trace.model_dump_json(exclude_defaults=True).encode()
                        ),
                    }
                )
        with TemporaryDirectory(prefix="lexinform-archive-storage-") as scratch:
            reads.allowed += (Path(scratch).resolve(),)
            history = measure_history(captures, Path(scratch))
    if reads.changed_inputs() or source_fingerprints() != pins:
        raise ValueError("Storage qualification source or corpus inputs changed")
    result: dict[str, Any] = {
        "version": 1,
        "status": "recorded",
        "cohort_sha256": digest(manifest),
        "synthetic_raw_sha256": {identity: digest(body) for identity, body in synthetics.items()},
        "index_sha256": INDEX_SHA256,
        "options": OPTIONS,
        "fixture_owner": {
            "term": 10,
            "number": "archive-storage-fixture",
            "purpose": "analysis",
            "synthetic": True,
        },
        "fixture_time": NOW.isoformat(),
        "counts": counts,
        "trace_bytes": trace_sizes,
        "history": history,
        "read_inputs": reads.initial_sha256,
        "source_sha256": pins,
        "exclusions": [
            "Technical fixture only; no real ownership, fleet forecast or cross-recipe comparison.",
            "Frozen 16-ZIP cohort plus four frozen synthetics; no ZIP-census claim.",
            "No semantic legal-role or omitted-tail independent gold labels.",
        ],
    }
    write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    result = run(args.corpus, args.cohort, args.output)
    print(json.dumps(result["history"]["delta"]))


if __name__ == "__main__":
    main()
