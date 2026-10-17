"""Evaluate frozen document-selection labels and optional batch evidence offline."""

import argparse
import datetime as dt
import gzip
import hashlib
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.models import TextDocument
from lexinform.sections import document_kind
from lexinform.services.documents import TextLoader
from tools.probe_document_evidence import EvidenceAnswer, recover_quote_span, validate_answer


class StudyFiles:
    def __init__(self, corpus: Path, candidates: list[dict[str, Any]]) -> None:
        self.corpus = corpus
        self.candidates = {candidate["source_url"]: candidate for candidate in candidates}

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
        return str(self.candidates[url]["document_id"]).encode()

    def extract(self, data: bytes) -> str:
        path = self.corpus / f"documents/text/{data.decode()}.txt.gz"
        return gzip.decompress(path.read_bytes()).decode()

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        raise AssertionError("Text-only study must not infer scan coverage")


def baseline(corpus: Path, case: dict[str, Any]) -> dict[str, Any]:
    candidates = case["question"]["candidates"]
    files = StudyFiles(corpus, candidates)
    loader = TextLoader({"legislacja.rcl.gov.pl": files.download}, files, max_bytes=32_000_000)
    documents = [
        TextDocument(
            url=c["source_url"], kind="rcl", version_basis=(f"rcl/{case['group']}/stage/study",)
        )
        for c in candidates
    ]
    document = documents[0].model_copy(
        update={"compare_versions": True, "alternatives": tuple(documents[1:])}
    )
    chosen, loaded = loader.select_document(document)
    preferred = [c["id"] for c in candidates if c["source_url"] == chosen.url]
    decision = (
        "prefer"
        if loaded.text and document_kind(loaded.text) == "bill"
        else ("ambiguous" if loaded.selection.state == "ambiguous" else "need_more")
    )
    if decision != "prefer":
        preferred = []
    expected = case["expected"]
    return {
        "decision": decision,
        "preferred_ids": preferred,
        "bundle_state": loaded.selection.state,
        "limitations": loaded.selection.limitations,
        "matches": decision in expected["decisions"]
        and sorted(preferred) == sorted(expected["preferred_ids"]),
    }


def verify_manifest(manifest: dict[str, Any], corpus: Path) -> None:
    cases = manifest["cases"]
    assert len(cases) == 60
    groups: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for case in cases:
        split = case["split"]
        counts[split] += 1
        assert groups.setdefault(case["group"], split) == split, "Related group leaked into holdout"
        if split != "holdout":
            continue
        for candidate in case["question"]["candidates"]:
            path = corpus / f"documents/text/{candidate['document_id']}.txt.gz"
            text = gzip.decompress(path.read_bytes()).decode()
            assert hashlib.sha256(text.encode()).hexdigest() == candidate["extraction_sha256"]
            raw = list((corpus / "documents/files").glob(candidate["document_id"] + ".*"))
            assert (
                len(raw) == 1
                and hashlib.sha256(raw[0].read_bytes()).hexdigest() == candidate["source_sha256"]
            )
            chunks = [text[start:end] for start, end in candidate["read_ranges"]]
            assert chunks == [block["text"] for block in candidate["blocks"]]
    assert counts == {"tuning": 20, "holdout": 40}


def citation_fact_errors(answer: EvidenceAnswer, question: dict[str, Any]) -> list[str]:
    months = (
        "stycznia",
        "lutego",
        "marca",
        "kwietnia",
        "maja",
        "czerwca",
        "lipca",
        "sierpnia",
        "września",
        "października",
        "listopada",
        "grudnia",
    )
    candidates = {candidate["id"]: candidate for candidate in question["candidates"]}
    errors: list[str] = []
    for fact in answer.facts:
        quotes = "\n".join(citation.quote for citation in fact.evidence)
        if fact.role == "bill" and not re.search(r"\bu\s*s\s*t\s*a\s*w\s*a\b", quotes, re.I):
            errors.append("bill role citation lacks its own heading")
        if fact.draft_date == "unknown":
            continue
        try:
            value = dt.date.fromisoformat(fact.draft_date)
        except ValueError:
            errors.append("invalid date")
            continue
        pattern = re.compile(
            rf"\b0?{value.day}(?:\s*[./-]\s*0?{value.month}\s*[./-]\s*{value.year}"
            rf"|\s+{months[value.month - 1]}\s+{value.year}|\s+0?{value.month}\s+{value.year})\b",
            re.I,
        )
        linked = False
        for citation in fact.evidence:
            candidate = candidates.get(fact.candidate_id)
            if candidate is None:
                continue
            block = next(
                (
                    block["text"]
                    for block in candidate["blocks"]
                    if block["id"] == citation.block_id
                ),
                "",
            )
            position = block.find(citation.quote)
            article = re.search(r"\bArt\.\s*1\b", block, re.I)
            linked = linked or bool(
                pattern.search(citation.quote)
                and 0 <= position < 1200
                and (article is None or position < article.start())
            )
        if not linked:
            errors.append("date citation lacks a heading date")
    return errors


def evaluate(
    manifest: dict[str, Any], corpus: Path, results: list[dict[str, Any]]
) -> dict[str, Any]:
    verify_manifest(manifest, corpus)
    cases = {case["id"]: case for case in manifest["cases"] if case["split"] == "holdout"}
    rules = {identity: baseline(corpus, case) for identity, case in cases.items()}
    profiles: dict[str, Counter[str]] = {}
    rows = []
    for result in results:
        case = cases[result["custom_id"].split("-", 1)[1]]
        message = result["result"]
        if message["type"] != "succeeded":
            rows.append({"id": result["custom_id"], "state": message["type"]})
            continue
        message = message["message"]
        model = message["model"]
        counts = profiles.setdefault(model, Counter())
        counts["requests"] += 1
        counts["input_tokens"] += message["usage"]["input_tokens"]
        counts["output_tokens"] += message["usage"]["output_tokens"]
        issues: list[str] = []
        try:
            answer = EvidenceAnswer.model_validate_json(
                "".join(b.get("text", "") for b in message["content"])
            )
            for fact in answer.facts:
                candidate = next(
                    (c for c in case["question"]["candidates"] if c["id"] == fact.candidate_id),
                    None,
                )
                if candidate is None:
                    continue
                blocks = {block["id"]: block["text"] for block in candidate["blocks"]}
                for citation in fact.evidence:
                    source = blocks.get(citation.block_id, "")
                    if citation.quote not in source:
                        span = recover_quote_span(source, citation.quote)
                        if span:
                            citation.quote = source[span[0] : span[1]]
                            counts["whitespace_recoveries"] += 1
            issues.extend(validate_answer(answer, case["question"]))
            issues.extend(citation_fact_errors(answer, case["question"]))
            if message["stop_reason"] != "end_turn":
                issues.append("incomplete output")
            expected = case["expected"]
            for fact in answer.facts:
                truth = expected["facts"].get(fact.candidate_id)
                if (
                    truth is None
                    or fact.role not in truth["role"]
                    or ("draft_date" in truth and fact.draft_date != truth["draft_date"])
                ):
                    issues.append("incorrect labelled fact")
            if {fact.candidate_id for fact in answer.facts} != set(expected["facts"]):
                issues.append("missing candidate facts")
            matches = answer.decision in expected["decisions"] and sorted(
                answer.preferred_ids
            ) == sorted(expected["preferred_ids"])
            correct = matches and not issues
            counts["correct"] += correct
            counts["wrong_preference"] += answer.decision == "prefer" and not matches
            counts["unsupported_preference"] += answer.decision == "prefer" and bool(issues)
            counts["additional_resolutions"] += correct and not rules[case["id"]]["matches"]
            rows.append(
                {
                    "id": result["custom_id"],
                    "model": model,
                    "issues": issues,
                    "correct": correct,
                    "answer": answer.model_dump(),
                }
            )
        except ValueError:
            counts["invalid_output"] += 1
            rows.append({"id": result["custom_id"], "model": model, "issues": ["invalid output"]})
    unresolved = sum(not row["matches"] or row["decision"] == "ambiguous" for row in rules.values())
    eligible = [
        model
        for model, counts in profiles.items()
        if counts["requests"] == 40
        and counts["wrong_preference"] == 0
        and counts["unsupported_preference"] == 0
        and unresolved > 0
        and counts["additional_resolutions"] / unresolved >= 0.2
    ]
    return {
        "manifest_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest(),
        "baseline": rules,
        "baseline_matches": sum(row["matches"] for row in rules.values()),
        "unresolved_baseline": unresolved,
        "profiles": profiles,
        "rows": rows,
        "e3_eligible_profiles": eligible,
        "models_evaluated": bool(profiles),
        "limitations": [
            "text-only cached baseline; PDF page coverage not inferred",
            "role recommendation is not bundle certification",
            "E3 role class still requires its own positive evidence; E5 live shadow is separate",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    report = evaluate(
        json.loads(args.manifest.read_bytes()),
        args.corpus,
        json.loads(args.results.read_bytes()) if args.results else [],
    )
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "baseline_matches",
                    "unresolved_baseline",
                    "profiles",
                    "e3_eligible_profiles",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
