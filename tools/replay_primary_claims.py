"""Replay pinned DOC citation presence without legal claims or retained source bodies."""

import argparse
import ast
import datetime as dt
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.models import reviews
from lexinform.models.analysis import Analysis, AnalysisRecord
from lexinform.models.enums import Category
from lexinform.models.reviews import DocumentRole, EvidenceCitation, ReviewContext, ReviewDocument
from lexinform.primary_claims import KeyChangeEvidence, validate_key_change_evidence
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.replay_doc_notes import DocManifest

ROOT = Path(__file__).resolve().parents[1]
BASE = "62f028fa11acce3aa2654b794428d74a9ca08bb7"
OLD_SHA = "c9c02f306e12c8146280fbfce51c063ad498ef70958516af33e442df68dcbc2c"
MANIFEST = ROOT / "docs/assets/audits/2026-11-04-doc-note-reader/input-manifest.json"
MANIFEST_SHA = "449d893ca8d9b89933a3ac84abd3b757d0f89298d06fc9ae6e5c31ecb38775d5"
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
    answer = AnalysisRecord(
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
            else:
                counts["excluded_unreadable"] += 1
                observations[case.sha256] = {"status": "unreadable"}
    if reads.changed_inputs() or source_fingerprints() != pins:
        raise ValueError("Replay inputs or source code changed")
    if counts["paths"] != 404 or len(observations) != 246 or not counts["eligible_sources"]:
        raise ValueError("Pinned DOC population or eligible coverage is incomplete")
    if any(not counts[name + "_negative_controls"] for name in EDITS):
        raise ValueError("A required quote-mutation class has no negative controls")
    report = {
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
