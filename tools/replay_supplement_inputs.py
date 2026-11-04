"""Replay pinned eligible supplements through local preparation without provider calls."""

import argparse
import gzip
import json
import logging
import re
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Never
from xml.sax.saxutils import escape

from tests.fakes import FakeBatchBackend, FakeLlm, FixedClock, make_analysis
from tests.harness import summary
from tests.unit.test_physical_extraction import docx

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    AnalysisRecord,
    BatchRequest,
    BillStatus,
    DigestItemMeta,
    TextDocument,
    supplement_kind,
)
from lexinform.models.enums import SourceKind
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisOptions, AnalysisService, Waiting
from lexinform.services.documents import TextLoader
from lexinform.services.sources import MetadataOnlySource, TextSources
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.replay_input_coverage import check_projection

NOW = datetime(2026, 11, 4, tzinfo=UTC)
EDITS = {
    "date": (r"\b(?:19|20)\d{2}\b", "2099"),
    "amount": (r"\b\d+(?:[.,]\d+)?\s*zł\b", "999999 zł"),
    "negation": (r"\b(?:jest|może|stosuje|przysługuje)\b", "nie jest"),
    "scope": (r"\b(?:cudzoziemc\w*|student\w*|pracownik\w*|obywatel\w*)\b", "wszystkich osób"),
    "reference": (r"\bart\.\s*\d+[a-z]?", "art. 999999"),
}


def refuse_client() -> Never:
    raise AssertionError("Offline preparation must not construct a provider client")


class PreparationOnly(FakeBatchBackend):
    def __init__(self) -> None:
        super().__init__()
        self.prepared: list[BatchRequest] = []
        self.backend = OpenAiAnalyzer(refuse_client, model="gpt-6.1-sol", clock=lambda: NOW)

    def prepare_request(self, request: BatchRequest) -> BatchRequest:
        self.prepared.append(request)
        return self.backend.prepare_request(request)

    def submit(self, requests: object) -> Never:
        raise AssertionError("Offline preparation must not submit")


def snapshot(
    data: bytes, *, parent: str, title: str, url: str, kind: SourceKind, budget: int
) -> tuple[dict[str, Any], str]:
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("Replay source exceeds its configured bound")
        return data

    loader = TextLoader({"api.sejm.gov.pl": download}, reader, max_bytes=32_000_000)
    document = TextDocument(url=url, kind=kind)
    _, file = loader.select_document(document)
    source_text = file.text or ""
    expected = TextBudget(budget).apply(source_text, KeywordPrefilter().spans(source_text))
    projection = expected.projection
    mapping = check_projection(projection, source_text) if projection is not None else None
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.upsert_summary(summary(parent, "Offline frozen prior context"), now=NOW)
        repo.save_analysis(
            10,
            parent,
            AnalysisRecord(
                analysis=make_analysis(),
                model="prior",
                prompt_version="prior",
                input_chars=1,
                truncated=False,
                text_source="pdf",
                created_at=NOW,
            ),
        )
        repo.set_status(10, parent, BillStatus.ANALYZED)
        bill = repo.get(10, parent)
        if bill is None:
            raise ValueError("Replay did not enroll its owner")
        backend = PreparationOnly()
        service = AnalysisService(
            repo,
            TextSources(MetadataOnlySource()),
            loader,
            FakeLlm(),
            FixedClock(NOW),
            AnalysisOptions(
                require_batch=True,
                batch_provider="openai",
                batch_models={"supplement": "gpt-6.1-sol"},
            ),
            text_budget=TextBudget(budget),
            batch=backend,
        )
        result = service.digest_supplement(bill, document, number="replay-extra", title=title)
        queued = repo.list_queued_batch_intents()
        core: dict[str, Any] = {
            "source_sha256": digest(data),
            "source_text_sha256": digest(source_text.encode()),
            "source_chars": len(source_text),
            "prepare_count": len(backend.prepared),
            "queued": len(queued),
            "waiting": isinstance(result, Waiting),
            "spent_usd": service.spent_usd,
            "calls": [call.model_dump(mode="json") for call in service.calls],
            "selection_sha256": digest(file.selection.without_text().model_dump_json().encode()),
        }
        receipt = None
        if queued:
            if len(queued) != 1 or len(backend.prepared) != 1:
                raise ValueError("One supplement must prepare exactly one frozen request")
            intent = queued[0]
            if not isinstance(intent.meta, DigestItemMeta):
                raise ValueError("Supplement metadata changed its purpose")
            ctx = intent.request.ctx
            if ctx.model_dump().get("text") != expected.text:
                raise ValueError(
                    "Runtime supplement text differs from the existing budget projection"
                )
            core.update(
                context_sha256=digest(ctx.model_dump_json().encode()),
                payload_sha256=intent.request.payload_sha256,
                request_sha256=digest(intent.request.model_dump_json().encode()),
                custom_id=intent.request.custom_id,
                memo_key=intent.meta.memo_key,
                text_sha256=digest(expected.text.encode()),
                text_chars=len(expected.text),
                legacy_meta_sha256=digest(
                    intent.meta.model_dump_json(exclude={"prepared_input_receipt"}).encode()
                ),
                projection_sha256=digest(repr(projection).encode()),
            )
            receipt = intent.meta.prepared_input_receipt
            if receipt is not None:
                if (
                    receipt.request_sha256 != intent.request.payload_sha256
                    or receipt.text_sha256 != digest(expected.text.encode())
                ):
                    raise ValueError("Receipt does not identify the actual frozen input")
                if (
                    not receipt.source_selection_id
                    or not receipt.artifact_sha256
                    or not receipt.extraction_id
                ):
                    raise ValueError("Prospective supplement receipt lost a required known parent")
                if projection is None:
                    raise ValueError("A supplement receipt requires its observed projection")
                for segment in receipt.segments:
                    if segment.origin == "source" and (
                        expected.text[segment.start : segment.end]
                        != source_text[segment.source_start : segment.source_end]
                    ):
                        raise ValueError("Receipt copied span differs from its actual source")
                repo.restore(repo.dump())
        dump = repo.dump().encode()
        return {
            "core": core,
            "mapping": mapping,
            "receipt": receipt.model_dump(mode="json") if receipt else None,
            "storage": {
                "sql_bytes": len(dump),
                "gzip_bytes": len(gzip.compress(dump, mtime=0)),
                "receipt_json_bytes": len(receipt.model_dump_json().encode()) if receipt else 0,
            },
        }, source_text


def run(corpus: Path, output: Path, *, index_sha256: str, baseline: Path | None) -> None:
    root = corpus.resolve(strict=True)
    target = output.absolute()
    if target.exists() or target.is_symlink() or target.resolve().is_relative_to(root):
        raise ValueError("Replay output must be new and outside the corpus")
    target.mkdir(parents=True)
    reads = CorpusReads(root)
    pins = source_fingerprints()
    script_sha256 = digest(read_artifact(Path(__file__)))
    results: dict[str, Any] = {}
    exclusions: list[dict[str, str]] = []
    with reads.guard():
        index_raw = read_artifact(root / "INDEX.json")
        if digest(index_raw) != index_sha256:
            raise ValueError("Corpus INDEX SHA mismatch")
        index = json.loads(index_raw)
        catalogue = json.loads(read_artifact(root / "sejm/term10/supplements/catalogue.json"))
        catalogue_by_number = {row["number"]: row for row in catalogue}
        eligible: list[tuple[dict[str, Any], bytes]] = []
        for number in index["cases"]["supplement.carries_document"]:
            record = json.loads(
                read_artifact(root / f"sejm/term10/supplements/records/{number}.json")
            )
            kind = supplement_kind(record["title"])
            if kind is None:
                exclusions.append(
                    {"number": number, "reason": "official_title_not_reader_eligible"}
                )
                continue
            official = catalogue_by_number.get(number, record)
            attachments = official.get("attachments", [])
            if len(attachments) != 1:
                exclusions.append({"number": number, "reason": "not_one_official_attachment"})
                continue
            filename = attachments[0]
            data = read_artifact(root / f"sejm/term10/supplements/files/{number}__{filename}")
            row = {
                "number": number,
                "parent": record["parent"],
                "title": record["title"],
                "kind": kind,
                "url": record.get(
                    "url", f"https://api.sejm.gov.pl/sejm/term10/prints/{number}/{filename}"
                ),
            }
            eligible.append((row, data))
        mutation_sources: dict[str, tuple[dict[str, Any], str, str]] = {}
        for row, data in eligible:
            for budget in (1_500_000, 12000):
                name = f"natural/{row['number']}/{budget}"
                result, text = snapshot(
                    data,
                    parent=row["parent"],
                    title=row["title"],
                    url=row["url"],
                    kind=row["kind"],
                    budget=budget,
                )
                results[name] = {
                    "population": "natural_eligible_supplement",
                    "descriptor": row,
                    **result,
                }
                if budget == 1_500_000:
                    for label, (pattern, replacement) in EDITS.items():
                        if label not in mutation_sources and re.search(pattern, text, re.I):
                            mutation_sources[label] = (row, text, replacement)
        for label, (row, text, replacement) in mutation_sources.items():
            pattern = EDITS[label][0]
            changed, count = re.subn(pattern, replacement, text, count=1, flags=re.I)
            if count != 1 or changed == text:
                raise ValueError("A meaningful mutation did not change its source")
            for variant, content in (("before", text), ("after", changed)):
                clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "\n", content)
                data = docx(escape(clean))
                result, _ = snapshot(
                    data,
                    parent=row["parent"],
                    title=row["title"],
                    url=row["url"] + ".controlled.docx",
                    kind=row["kind"],
                    budget=1_500_000,
                )
                results[f"mutation/{label}/{variant}"] = {
                    "population": "synthetic_mutation_of_real_supplement_text",
                    "descriptor": row,
                    **result,
                }
            before, after = (
                results[f"mutation/{label}/{variant}"]["core"] for variant in ("before", "after")
            )
            for field in (
                "text_sha256",
                "context_sha256",
                "payload_sha256",
                "custom_id",
                "memo_key",
            ):
                if before.get(field) == after.get(field):
                    raise ValueError(f"Mutation {label} did not change {field}")
    changed_inputs = reads.changed_inputs()
    if (
        changed_inputs
        or source_fingerprints() != pins
        or digest(read_artifact(Path(__file__))) != script_sha256
    ):
        raise ValueError("Replay corpus/source files changed")
    if set(mutation_sources) != set(EDITS):
        raise ValueError("The five mutation classes were not exercised")
    paired = None
    if baseline is not None:
        prior = json.loads(read_artifact(baseline))
        if prior["script_sha256"] != script_sha256:
            raise ValueError("Paired replay used different replay code")
        if prior["input_sha256"] != reads.initial_sha256 or set(prior["results"]) != set(results):
            raise ValueError("Paired replay used different corpus inputs or cases")
        mismatches = [
            name
            for name in results
            if results[name]["core"] != prior["results"][name]["core"]
            or results[name]["mapping"] != prior["results"][name]["mapping"]
        ]
        if mismatches:
            raise ValueError(f"Paired text/context/payload/memo/projection changed: {mismatches}")
        paired = {
            "baseline_sha256": digest(read_artifact(baseline)),
            "cases": len(results),
            "mismatches": [],
            "storage_delta": {
                name: {
                    field: results[name]["storage"][field]
                    - prior["results"][name]["storage"][field]
                    for field in ("sql_bytes", "gzip_bytes")
                }
                for name in results
            },
        }
    report = {
        "suite": "supplement-input-v1",
        "index_sha256": index_sha256,
        "script_sha256": script_sha256,
        "source_sha256": pins,
        "input_sha256": reads.initial_sha256,
        "changed_inputs": changed_inputs,
        "results": results,
        "excluded": exclusions,
        "paired": paired,
        "counts": dict(
            Counter(
                "receipted" if result["receipt"] else "unstamped" for result in results.values()
            )
        ),
        "limits": [
            "Existing INDEX carries_document set only; scanned/unreadable population excluded",
            "Controlled prior analysis; no historical paid request is reconstructed",
            "Only local actual provider preparation; no submit/model/client calls",
        ],
    }
    write_json(target / "report.json", report)
    print(
        json.dumps(
            {
                "cases": len(results),
                "counts": report["counts"],
                "paired": paired,
                "output": str(target),
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index-sha256", required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    run(args.corpus, args.output, index_sha256=args.index_sha256, baseline=args.baseline)


if __name__ == "__main__":
    main()
