"""Replay pinned DOC citation presence without legal claims or retained source bodies."""

import argparse
import ast
import datetime as dt
import gzip
import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.input_receipts import primary_input_receipt
from lexinform.models import reviews
from lexinform.models.analysis import Analysis, AnalysisRecord, BillContext
from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.batch import AnalysisQuestion, BatchIntent, BatchItemMeta, BatchRequest
from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.models.enums import ApplicantType, Category
from lexinform.models.input_receipts import PrimaryInputReceipt
from lexinform.models.reviews import DocumentRole, EvidenceCitation, ReviewContext, ReviewDocument
from lexinform.primary_claim_input import validate_key_change_input
from lexinform.primary_claims import KeyChangeEvidence, validate_key_change_evidence
from lexinform.text_projection import SourcePiece, TextProjection, project_pieces
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.replay_doc_notes import DocManifest

ROOT = Path(__file__).resolve().parents[1]
BASE = "62f028fa11acce3aa2654b794428d74a9ca08bb7"
OLD_SHA = "c9c02f306e12c8146280fbfce51c063ad498ef70958516af33e442df68dcbc2c"
MANIFEST = ROOT / "docs/assets/audits/2026-11-04-doc-note-reader/input-manifest.json"
MANIFEST_SHA = "449d893ca8d9b89933a3ac84abd3b757d0f89298d06fc9ae6e5c31ecb38775d5"
BASELINE = ROOT / "docs/assets/audits/2026-11-04-primary-claim-evidence/corpus-report.json.gz"
BASELINE_SHA = "b10fa8357a4e6cab3dbb8bd88d1b5aefdb1c85a7c1dfab6ed9bf9b60c17610a2"
Matcher = Callable[
    [EvidenceCitation | None, ReviewContext, set[DocumentRole]], ReviewDocument | None
]
ART = re.compile(r"\bart\.\s*\d+[a-z]?", re.I)
EDITS = {
    "date": (r"\b(?:19|20)\d{2}\b", "2099"),
    "amount": (r"\b\d+(?:[.,]\d+)?\s*zł\b", "999999 zł"),
    "negation": (r"\b(?:jest|może|stosuje|przysługuje)\b", "nie jest"),
    "scope": (r"\b(?:cudzoziemc\w*|student\w*|pracownik\w*|obywatel\w*)\b", "wszystkich osób"),
    "reference": (r"\bart\.\s*\d+[a-z]?", "art. 999999"),
}


def frozen_matcher(raw: bytes, current: bytes) -> Matcher:
    if digest(raw) != OLD_SHA:
        raise ValueError("Frozen review source SHA differs")
    old, new = ast.parse(raw), ast.parse(current)
    excluded = {"citation_document", "citation_document_in"}
    globals_before = [
        ast.dump(node) for node in old.body if getattr(node, "name", "") not in excluded
    ]
    globals_after = [
        ast.dump(node) for node in new.body if getattr(node, "name", "") not in excluded
    ]
    if globals_before != globals_after:
        raise ValueError("Review globals differ from the frozen baseline")
    function = next(node for node in old.body if getattr(node, "name", "") == "citation_document")
    namespace = dict(vars(reviews))
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "pinned-review-citation", "exec"),
        namespace,
    )
    return cast(Matcher, namespace["citation_document"])


def technical_answer(text: str) -> AnalysisRecord:
    return AnalysisRecord(
        analysis=Analysis(
            relevant=True,
            score=1,
            category=Category.NONE,
            summary="Offline technical check",
            key_changes=["Offline technical check"],
            practical_impact="Unassessed",
            confidence=0,
            rationale="No legal assessment",
        ),
        model="offline",
        prompt_version="offline",
        input_chars=len(text),
        truncated=False,
        text_source="documents",
        created_at=dt.datetime(2026, 11, 4, tzinfo=dt.UTC),
    )


def check_text(text: str, identity: str, old: Matcher, counts: Counter[str]) -> dict[str, Any]:
    found = ART.search(text)
    if found is None:
        counts["excluded_no_art_locator"] += 1
        return {"status": "no_art_locator", "text_sha256": digest(text.encode())}
    quote = text[found.start() : found.end() + 400]
    locator = " ".join(found.group().split())
    source = ReviewDocument(
        identity=identity,
        url="offline://" + identity,
        role="project_text",
        text=text,
        sha256=digest(text.encode()),
        normalized_sha256="",
        complete=False,
    )
    context = ReviewContext(
        number="offline",
        title="Technical parity",
        purpose="consultation_review",
        documents=(source,),
    )
    answer = technical_answer(text)
    normalized_source = " ".join(text.split())

    def compare(value: str, expected: bool) -> None:
        citation = EvidenceCitation(document_id=identity, locator=locator, quote=value)
        results = (
            old(citation, context, {"project_text"}),
            reviews.citation_document(citation, context, {"project_text"}),
            reviews.citation_document_in(citation, (source,), {"project_text"}),
        )
        if any((item is source) != expected for item in results):
            raise ValueError("Frozen/wrapper/helper citation parity differs")
        counts["parity_checks"] += 1
        envelope = KeyChangeEvidence(
            0, digest(answer.analysis.key_changes[0].encode()), source.sha256, citation
        )
        checked = validate_key_change_evidence(answer, envelope, (source,))
        allowed = {"quote_present", "quote_ambiguous"} if expected else {"citation_mismatch"}
        if (
            checked.status not in allowed
            or checked.semantic != "semantic_not_assessed"
            or checked.coverage != "partial"
            or checked.block_origins != "unmapped"
            or checked.paid_origins != "unmapped"
        ):
            raise ValueError("Offline claim check exceeded its technical evidence boundary")
        counts[checked.status] += 1

    compare(quote, True)
    for name, (pattern, replacement) in EDITS.items():
        target = re.search(pattern, quote[len(found.group()) :], re.I)
        if target is None:
            counts[name + "_ineligible"] += 1
            continue
        start, end = target.span()
        start, end = start + len(found.group()), end + len(found.group())
        if name == "negation":
            replacement = "nie " + target.group()
        changed = quote[:start] + replacement + quote[end:]
        if " ".join(changed.split()) in normalized_source:
            counts[name + "_excluded_still_present"] += 1
            continue
        compare(changed, False)
        counts[name + "_negative_controls"] += 1
    counts["eligible_sources"] += 1
    return {"status": "checked", "text_sha256": source.sha256}


def check_input_maps(text: str, identity: str, path: str, data: bytes) -> dict[str, Any] | None:
    found = ART.search(text)
    if found is None:
        return None
    start, end = found.start(), min(len(text), found.end() + 400)
    quote = text[start:end]
    if start == 0 or end == len(text) or text.count(quote) != 1:
        return None
    url = "offline://corpus/" + path
    source = ReviewDocument(
        identity=identity,
        url=url,
        role="project_text",
        text=text,
        sha256=digest(text.encode()),
        normalized_sha256="",
        complete=False,
        file_sha256=digest(data),
    )
    answer = technical_answer(text)
    evidence = KeyChangeEvidence(
        0,
        digest(answer.analysis.key_changes[0].encode()),
        source.sha256,
        EvidenceCitation(document_id=identity, locator=found.group(), quote=quote),
    )
    if validate_key_change_evidence(answer, evidence, (source,)).observed_text_span != (start, end):
        return None
    selection = DocumentSelection(
        parts=(
            DocumentPart(
                url=url,
                sha256=digest(data),
                role="bill",
                text=text,
                text_end=len(text),
                text_sha256=source.sha256,
            ),
        )
    )
    middle = (start + end) // 2
    variants: dict[str, tuple[SourcePiece, ...]] = {
        "full": (SourcePiece(text, 0),),
        "cut_outside": (SourcePiece(text[start:], start),),
        "cut_quote": (SourcePiece(text[:start], 0), SourcePiece(text[end:], end)),
        "cut_inside": (SourcePiece(text[:middle], 0), SourcePiece(text[middle + 1 :], middle + 1)),
        "generated_copy": (SourcePiece(quote),),
        "generated_gap": (
            SourcePiece(text[:middle], 0),
            SourcePiece(" GAP "),
            SourcePiece(text[middle:], middle),
        ),
        "repeated_mapping": (
            SourcePiece(quote, start),
            SourcePiece(" GAP "),
            SourcePiece(quote, start),
        ),
    }
    results: dict[str, Any] = {}
    for name, pieces in variants.items():
        projection = project_pieces(text, pieces)
        for segment in projection.segments:
            if segment.source_start is not None:
                stop = segment.source_start + segment.end - segment.start
                if (
                    text[segment.source_start : stop]
                    != projection.text[segment.start : segment.end]
                ):
                    raise ValueError("Independent projection slice check failed")
        intent = input_intent(selection, data, projection)
        actual = answer.model_copy(
            update={
                "input_chars": len(projection.text),
                "request_sha256": intent.request.payload_sha256,
                "source_url": url,
                "source_kind": "print",
            }
        )
        expected = (
            "retained"
            if name in ("full", "cut_outside")
            else "unknown"
            if name == "repeated_mapping"
            else "omitted"
        )
        cases = [(name, actual, (source,), intent, expected)]
        if name == "generated_gap":
            receipt = intent.meta.prepared_input_receipt
            assert receipt is not None, "the technical primary intent has its built receipt"
            prefix = PrimaryInputReceipt.model_validate(
                {
                    **receipt.model_dump(),
                    "retention": "prefix",
                    "reason": "segment_limit",
                    "segments": receipt.segments[:1],
                }
            )
            partial = BatchIntent.model_validate(
                {
                    **intent.model_dump(),
                    "meta": intent.meta.model_copy(update={"prepared_input_receipt": prefix}),
                }
            )
            cases.append(("prefix", actual, (source,), partial, "unknown"))
        if name == "full":
            cases.extend(
                [
                    (
                        "raw_file_hash_mismatch",
                        actual,
                        (source.model_copy(update={"file_sha256": "a" * 64}),),
                        intent,
                        "unknown",
                    ),
                    (
                        "request_hash_mismatch",
                        actual.model_copy(update={"request_sha256": "a" * 64}),
                        (source,),
                        intent,
                        "unknown",
                    ),
                    (
                        "source_text_hash_mismatch",
                        actual,
                        (source.model_copy(update={"sha256": "a" * 64}),),
                        intent,
                        "unknown",
                    ),
                ]
            )
        for label, record, documents, frozen, status in cases:
            checked = validate_key_change_input(record, evidence, documents, intent=frozen)
            if (
                checked.status != status
                or checked.semantic != "semantic_not_assessed"
                or checked.block_origins != "unmapped"
                or checked.physical_origins != "unmapped"
            ):
                raise ValueError(
                    f"Technical input-map case {label} exceeded its expected boundary: {checked}"
                )
            if checked.input_span is not None:
                begin, stop = checked.input_span
                if projection.text[begin:stop] != quote:
                    raise ValueError("Observed input span does not round-trip its literal quote")
                if not any(
                    segment.source_start is not None
                    and segment.source_start <= start
                    and segment.source_start + segment.end - segment.start >= end
                    and begin == segment.start + start - segment.source_start
                    for segment in projection.segments
                ):
                    raise ValueError("Observed input span lacks independent source coordinates")
            results[label] = {
                "status": checked.status,
                "reason": checked.reason,
                "input_span": checked.input_span,
                "evidence_status": checked.evidence.status,
                "source_sha256": source.sha256,
                "emitted_sha256": digest(projection.text.encode()),
                "request_sha256": frozen.request.payload_sha256,
            }
    return {
        "population": "synthetic_frozen_primary_inputs_over_real_extracted_doc_text",
        "selection": "First ordered SHA with unique literal Art quote and surrounding text",
        "counts": dict(Counter(case["status"] for case in results.values())),
        "source_file_sha256": identity,
        "source_path": path,
        "source_text_sha256": source.sha256,
        "source_chars": len(text),
        "cases": results,
    }


def input_intent(
    selection: DocumentSelection, data: bytes, projection: TextProjection
) -> BatchIntent:
    ctx = BillContext(
        number="offline",
        title="Technical input-map replay",
        description=None,
        document_date=None,
        applicant_type=ApplicantType.UNKNOWN,
        text=projection.text,
        truncated=True,
        text_source="documents",
        source_kind="print",
    )
    request = BatchRequest(
        custom_id="offline-primary-map",
        term=10,
        number="offline",
        question=AnalysisQuestion(kind="analysis", ctx=ctx),
    ).freeze(
        payload_json=ctx.model_dump_json(),
        model="offline",
        prompt_version="offline",
        estimated_cost_usd=0,
    )
    receipt = primary_input_receipt(
        request,
        selection,
        projection=projection,
        body=ArtifactIdentity(sha256=digest(data), byte_size=len(data)),
    )
    return BatchIntent.from_request(
        request=request,
        meta=BatchItemMeta(
            input_chars=len(projection.text),
            truncated=True,
            text_source="documents",
            source_kind="print",
            source_url=selection.parts[0].url,
            revision=1,
            text_sha256=None,
            selection=selection.without_text(),
            memo_key=request.custom_id,
            request_sha256=request.payload_sha256,
            prepared_input_receipt=receipt,
        ),
        provider="openai",
        created_at=dt.datetime(2026, 11, 4, tzinfo=dt.UTC),
    )


def run(corpus: Path, output: Path) -> dict[str, Any]:
    if output.is_symlink():
        raise ValueError("Output cannot be a symbolic link")
    corpus, output = corpus.resolve(), output.resolve()
    if output.is_relative_to(corpus) or output.exists():
        raise ValueError("Output must be new and outside the read-only corpus")
    manifest_bytes = read_artifact(MANIFEST)
    if digest(manifest_bytes) != MANIFEST_SHA:
        raise ValueError("Pinned DOC manifest differs")
    manifest = DocManifest.model_validate_json(manifest_bytes)
    raw = subprocess.check_output(
        ["git", "show", BASE + ":src/lexinform/models/reviews.py"], cwd=ROOT
    )
    old = frozen_matcher(raw, read_artifact(Path(reviews.__file__)))
    pins = source_fingerprints()
    reads, counts = CorpusReads(corpus), Counter[str]()
    observations: dict[str, dict[str, Any]] = {}
    input_maps: dict[str, Any] | None = None
    with reads.guard():
        if digest(read_artifact(corpus / "INDEX.json")) != manifest.index_sha256:
            raise ValueError("Corpus INDEX differs from the pinned cohort")
        for case in manifest.cases:
            if case.extension != ".doc":
                continue
            data = read_artifact(corpus / case.path)
            if digest(data) != case.sha256:
                raise ValueError("Pinned DOC input differs")
            counts["paths"] += 1
            if case.sha256 in observations:
                continue
            text = DocTextExtractor().extract(data)
            if text.strip():
                observations[case.sha256] = check_text(text, case.sha256, old, counts)
                if input_maps is None:
                    input_maps = check_input_maps(text, case.sha256, case.path, data)
            else:
                counts["excluded_unreadable"] += 1
                observations[case.sha256] = {"status": "unreadable"}
    if reads.changed_inputs() or source_fingerprints() != pins:
        raise ValueError("Replay inputs or source code changed")
    if counts["paths"] != 404 or len(observations) != 246 or not counts["eligible_sources"]:
        raise ValueError("Pinned DOC population or eligible coverage is incomplete")
    if any(not counts[name + "_negative_controls"] for name in EDITS):
        raise ValueError("A required quote-mutation class has no negative controls")
    baseline_raw = read_artifact(BASELINE)
    if digest(baseline_raw) != BASELINE_SHA:
        raise ValueError("Pinned primary-claim baseline differs")
    baseline = json.loads(gzip.decompress(baseline_raw))
    if baseline["counts"] != dict(counts) or baseline["observations"] != observations:
        raise ValueError("Existing quote-parity observations or counts changed")
    if baseline["inputs"] != reads.initial_sha256 or input_maps is None:
        raise ValueError("Existing corpus input population changed or map sample unavailable")
    report = {
        "schema_version": 2,
        "baseline_report_sha256": BASELINE_SHA,
        "legacy_parity_unchanged": True,
        "input_maps": input_maps,
        "manifest_sha256": MANIFEST_SHA,
        "index_sha256": manifest.index_sha256,
        "baseline_ref": BASE,
        "baseline_source_sha256": OLD_SHA,
        "source_fingerprints": pins,
        "inputs": reads.initial_sha256,
        "counts": dict(sorted(counts.items())),
        "observations": observations,
        "limitations": [
            "Synthetic project_text role/context/claim test technical parity only.",
            "No legal classification, primary citations, human labels or semantic assessment.",
            "No excerpts or raw bodies retained; only pinned 404 DOC paths/246 SHAs covered.",
            "Unreadable/no-Art sources and ineligible mutation classes are excluded explicitly.",
            "Synthetic coverage stays partial; paid and block origins remain unmapped.",
            "Input-map controls use one eligible DOC; roles and frozen requests are synthetic.",
            "No provider preparation/submission or historical paid-input reconstruction occurs.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.corpus, args.output)
    print(report["counts"])


if __name__ == "__main__":
    main()
