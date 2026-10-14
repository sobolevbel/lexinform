"""Replay document evidence and source roles offline against a downloaded public corpus."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from lexinform.models import (
    ConsultationFinding,
    DocumentReview,
    EffectiveRule,
    EvidenceCitation,
    RclProject,
    ReviewContext,
    ReviewDocument,
    validate_review,
)
from lexinform.services.analysis import text_digest
from lexinform.telegram_text import split_html


def audit(
    corpus: Path, response: Path, output: Path, final_act: Path | None = None
) -> dict[str, object]:
    index_file = corpus / "INDEX.json"
    index = json.loads(index_file.read_text())
    counts: Counter[str] = Counter()
    for identity, entity in index["entities"].items():
        if not identity.startswith("rcl:"):
            continue
        project_file = corpus / entity["project"]
        if not project_file.is_file():
            counts["missing_metadata"] += 1
            continue
        project = RclProject.model_validate_json(project_file.read_text())
        counts["rcl_projects"] += 1
        for role in ("response", "positions"):
            documents = [
                doc
                for stage in project.stages
                if stage.is_consultation and stage.reached
                for doc in stage.documents(role)
            ]
            counts[f"{role}_projects"] += bool(documents)
            counts[f"{role}_documents"] += len(documents)
            counts["unreadable_metadata_documents"] += sum(not doc.readable for doc in documents)
    text = response.read_text()
    fixtures = json.loads(
        Path("tests/fixtures/message_usefulness/review-evidence.json").read_text()
    )
    context = ReviewContext(
        number="RCL/12403400",
        title="Wyroby budowlane",
        purpose="consultation_review",
        documents=(
            ReviewDocument(
                identity="response",
                url=fixtures[0]["source"],
                role="response",
                text=text,
                sha256=hashlib.sha256(text.encode()).hexdigest(),
                normalized_sha256=text_digest(text),
            ),
        ),
    )
    checked: list[dict[str, object]] = []
    mutations = 0
    for item in fixtures:
        citation = EvidenceCitation(
            document_id="response", locator=item["locator"], quote=item["quote"]
        )
        finding = ConsultationFinding(
            proposal=item["locator"],
            respondent="",
            outcome=item["outcome"],
            reason="",
            affected_groups=(),
            evidence=citation,
            text_change=None,
            text_evidence=None,
        )
        review = DocumentReview(
            purpose="consultation_review",
            complete=True,
            summary="",
            consultation_findings=(finding,),
            effective_rules=(),
            unresolved=(),
        )
        observed = validate_review(review, context).consultation_findings[0].outcome
        assert observed == item["outcome"]
        checked.append(
            {"locator": item["locator"], "expected": item["outcome"], "observed": observed}
        )
        for prefix in (
            "Data 2026-12-01. ",
            "Kwota 200 PLN. ",
            "Nie uwzględniono. ",
            "Dotyczy tylko cudzoziemców. ",
            "Art. 15 ust. 2. ",
        ):
            altered = finding.model_copy(
                update={"evidence": citation.model_copy(update={"quote": prefix + citation.quote})}
            )
            invalid = review.model_copy(update={"consultation_findings": (altered,)})
            assert validate_review(invalid, context).consultation_findings[0].outcome == "unknown"
            mutations += 1
    # The full report remains available; the sender never drops a table's qualifications.
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts = split_html(escaped)
    assert "".join(parts) == escaped
    final_cases: list[dict[str, object]] = []
    if final_act is not None:
        final_text = final_act.read_text()
        final_fixtures = json.loads(
            Path("tests/fixtures/message_usefulness/act-rule-evidence.json").read_text()
        )
        act_context = ReviewContext(
            number="act-sample",
            title="Prawo komunikacji elektronicznej",
            purpose="act_application",
            act_eli="DU/2024/1222",
            documents=(
                ReviewDocument(
                    identity="final-act",
                    url=final_fixtures[0]["source"],
                    role="final_act",
                    text=final_text,
                    sha256=hashlib.sha256(final_text.encode()).hexdigest(),
                    normalized_sha256=text_digest(final_text),
                ),
            ),
        )
        for item in final_fixtures:
            citation = EvidenceCitation(
                document_id="final-act", locator=item["locator"], quote=item["quote"]
            )
            rule = EffectiveRule.model_validate(
                dict(
                    locator=item["locator"],
                    change="Confirmed special date",
                    affected_groups=[],
                    conditions=[],
                    exceptions=[],
                    old_applications="",
                    documents=[],
                    date_kind="absolute",
                    date=item["date"],
                    days_after_promulgation=None,
                    trigger="",
                    evidence=citation,
                    date_evidence=citation,
                )
            )
            output_review = DocumentReview(
                purpose="act_application",
                complete=True,
                summary="",
                consultation_findings=(),
                effective_rules=(rule,),
                unresolved=(),
            )
            validated = validate_review(output_review, act_context)
            assert validated.effective_rules[0].date == rule.date
            final_cases.append({"locator": item["locator"], "date": item["date"]})
        transitions = json.loads(
            Path("tests/fixtures/message_usefulness/act-application-evidence.json").read_text()
        )
        for item in transitions:
            citation = EvidenceCitation(
                document_id="final-act", locator=item["locator"], quote=item["quote"]
            )
            rule = EffectiveRule.model_validate(
                {
                    **{
                        key: item[key]
                        for key in (
                            "locator",
                            "change",
                            "affected_groups",
                            "conditions",
                            "exceptions",
                            "old_applications",
                            "date_kind",
                            "date",
                            "trigger",
                        )
                    },
                    "documents": [],
                    "days_after_promulgation": None,
                    "evidence": citation,
                    "date_evidence": citation,
                }
            )
            transition = DocumentReview(
                purpose="act_application",
                complete=True,
                summary="",
                consultation_findings=(),
                effective_rules=(rule,),
                unresolved=(),
            )
            validated = validate_review(transition, act_context)
            assert validated.effective_rules[0].conditions == rule.conditions
            assert validated.effective_rules[0].date is None
            final_cases.append(
                {
                    "locator": item["locator"],
                    "conditions": list(rule.conditions),
                    "date_kind": validated.effective_rules[0].date_kind,
                }
            )
    manifest: dict[str, object] = {
        "corpus_index_sha256": hashlib.sha256(index_file.read_bytes()).hexdigest(),
        "source_counts": dict(counts),
        "response_text_sha256": context.documents[0].sha256,
        "response_file_sha256": fixtures[0]["file_sha256"],
        "response_characters": len(text),
        "response_rows": sum("\t" in row for row in text.splitlines()),
        "independent_manual_cases": checked,
        "final_act_date_cases": final_cases,
        "rejected_changed_quotes": mutations,
        "lossless_full_response_parts": len(parts),
        "exclusions": [
            "Other report bodies were not downloaded or independently labelled.",
            "Sejm live reports page returned Human Verification; no CAPTCHA bypass.",
            "No paid model evaluation and no measured reader score.",
        ],
    }
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("response_text", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--final-act-text", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            audit(args.corpus, args.response_text, args.output, args.final_act_text),
            ensure_ascii=False,
            indent=2,
        )
    )
