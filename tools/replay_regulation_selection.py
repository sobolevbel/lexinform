"""Replay blocked source selections and semantic mutations against pinned local originals."""

import argparse
import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.document_candidates import (
    CandidateSet,
    DocumentCandidate,
    SelectedCandidate,
    select_candidate_versions,
)
from lexinform.keywords import KeywordPrefilter, accept_text_hits
from lexinform.models import BillStatus
from lexinform.services.documents import TextLoader
from lexinform.services.sources import RclTextSource
from lexinform.services.text_prefilter import TextPrefilterService
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints


class ReplayClock:
    def now(self) -> dt.datetime:
        return dt.datetime(2026, 11, 20, 9, tzinfo=dt.UTC)


def replay(inputs: Path) -> dict[str, object]:
    inputs = inputs.resolve(strict=True)
    manifest = json.loads(read_artifact(inputs / "manifest.json"))
    state = read_artifact(inputs / "state.sql")
    if digest(state) != manifest["state_sha256"]:
        raise ValueError("State snapshot SHA mismatch")
    reads = CorpusReads(inputs)
    source = source_fingerprints()
    records: list[dict[str, object]] = []
    mutations: dict[str, dict[str, bool]] = {}

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        name = url.rsplit("/", 1)[-1]
        data = (inputs / name).read_bytes()
        if digest(data) != manifest["inputs"][name]:
            raise ValueError(f"Original SHA mismatch: {name}")
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError(f"Source exceeds the configured limit: {name}")
        return data

    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    loader = TextLoader({"legislacja.rcl.gov.pl": download}, extractor, max_bytes=32_000_000)
    repo = SqliteBillRepository(":memory:")
    try:
        repo.restore(state.decode())
        blocked = repo.list_by_status(
            [BillStatus.DOCUMENT_AMBIGUOUS, BillStatus.DOCUMENT_UNSUPPORTED], limit=-1
        )
        if not blocked or repo.list_by_status([BillStatus.TEXT_PREFILTER_PENDING], limit=1):
            raise ValueError("Expected a blocked-only replay queue")
        texts = RclTextSource()
        with reads.guard():
            for bill in blocked:
                document = texts.locate(bill).document
                if document is None:
                    raise ValueError(f"No source for {bill.number}")
                selected, loaded = loader.select_document(
                    document, rcl_project=bill.rcl, proposal_analysis=True
                )
                hits = KeywordPrefilter().match_counts(loaded.text or "")
                records.append(
                    {
                        "number": bill.number,
                        "selected": selected.url,
                        "state": loaded.selection.state,
                        "chars": len(loaded.text or ""),
                        "text_sha256": digest((loaded.text or "").encode()),
                        "reasons": loaded.selection.rejected,
                        "hits": hits,
                        "accepted": accept_text_hits(hits, min_distinct=2, min_occurrences=3),
                    }
                )
                if not document.regulation:
                    continue
                raw = loader.read(document.url)
                parts = raw.selection.observed_parts or raw.selection.parts
                for index, part in enumerate(parts):
                    base = part.text + (
                        "\n§ 999. Cudzoziemiec nie wnosi opłaty 200,50 zł. "
                        "Termin upływa 1 grudnia 2026 r. Stosuje się art. 2 ust. 1."
                    )
                    checks = {}
                    for name, old, new in (
                        ("date", "1 grudnia 2026", "2 grudnia 2026"),
                        ("amount", "200,50 zł", "300,50 zł"),
                        ("negation", "nie wnosi opłaty", "wnosi opłaty"),
                        ("scope", "Cudzoziemiec nie", "Obywatel nie"),
                        ("reference", "Stosuje się art. 2", "Stosuje się art. 3"),
                    ):
                        choice = select_candidate_versions(
                            CandidateSet(
                                (
                                    DocumentCandidate("a", base),
                                    DocumentCandidate("b", base.replace(old, new)),
                                )
                            ),
                            regulation=True,
                        )
                        if choice is not None:
                            checks[name] = not isinstance(choice, SelectedCandidate)
                    if checks:
                        mutations[f"{bill.number}/{index}"] = checks
            service = TextPrefilterService(
                repo, texts, loader, KeywordPrefilter(), clock=ReplayClock()
            )
            recovery = asdict(service.run(limit=len(blocked)))
            statuses = {
                bill.number: current.status.value
                for bill in blocked
                if (current := repo.get(bill.term, bill.number)) is not None
            }
            repo.restore(repo.dump())
            repeat = asdict(service.run(limit=len(blocked)))
        if reads.changed_inputs() or source != source_fingerprints():
            raise ValueError("Inputs or source code changed during replay")
        return {
            "source_sha256": source,
            "input_manifest_sha256": digest(read_artifact(inputs / "manifest.json")),
            "state_sha256": manifest["state_sha256"],
            "read_inputs": reads.initial_sha256,
            "selections": records,
            "mutations": mutations,
            "recovery": recovery,
            "statuses": statuses,
            "after_restart": repeat,
            "exclusions": [
                "This replay makes no model calls and does not establish relevance scores."
            ],
        }
    finally:
        repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.inputs.resolve()):
        parser.error("Output must be new and outside the input directory")
    result = replay(args.inputs)
    write_json(args.output, result)
    print(json.dumps({key: result[key] for key in ("recovery", "statuses", "after_restart")}))


if __name__ == "__main__":
    main()
