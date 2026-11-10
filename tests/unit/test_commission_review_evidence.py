import datetime as dt
from pathlib import Path

import pytest

from lexinform.commission_reviews import commission_text_matches
from lexinform.models import (
    ReviewContext,
    ReviewDocument,
    validate_review,
)
from lexinform.models.reviews import DocumentRole
from tests.commission_cases import (
    AFTER,
    BEFORE,
    RESPONSE,
    citation,
    context,
    document,
    finding,
    review,
)
from tests.harness import RCL


def test_acceptance_and_incorporation_require_three_separate_sources() -> None:
    checked = validate_review(review(), context())
    assert checked.complete
    assert checked.commission_findings == (finding(),)
    assert not checked.consultation_findings
    assert not checked.effective_rules


@pytest.mark.parametrize("role", ["commission_position", "project_text"])
def test_position_and_changed_text_never_prove_acceptance(role: DocumentRole) -> None:
    ctx = context()
    ctx = ctx.model_copy(
        update={
            "documents": tuple(
                doc.model_copy(update={"role": role}) if doc.identity == "response" else doc
                for doc in ctx.documents
            )
        }
    )
    checked = validate_review(review(), ctx)
    item = checked.commission_findings[0]
    assert item.outcome == "unknown" and item.text_change is None
    assert item.evidence is None and not item.reason and not item.respondent
    assert item.practical_impact and item.proposal_evidence
    assert not checked.complete


@pytest.mark.parametrize(
    "response",
    [
        "Art. 1. Uwaga nieuwzględniona.",
        "Art. 1. Uwaga nie uwzględniona.",
        "Art. 1. Uwaga uwzględniona częściowo.",
        "Art. 1. Proponuje się uwzględnienie uwagi.",
        "Art. 11. Uwaga uwzględniona.",
    ],
)
def test_negative_partial_proposed_or_other_provision_is_not_acceptance(response: str) -> None:
    ctx = context()
    ctx = ctx.model_copy(
        update={
            "documents": tuple(
                document("response", "commission_response", response)
                if doc.identity == "response"
                else doc
                for doc in ctx.documents
            )
        }
    )
    item = finding().model_copy(
        update={
            "evidence": citation(
                "response", response, "Art. 11." if "Art. 11." in response else "Art. 1."
            )
        }
    )
    checked = validate_review(review(item), ctx)
    assert checked.commission_findings[0].outcome == "unknown"
    assert checked.commission_findings[0].text_change is None


@pytest.mark.parametrize("replacement", ["300 PLN", "nie uwzględniona", "Art. 11.", "obywateli UE"])
def test_changed_amount_negation_reference_or_scope_breaks_exact_evidence(replacement: str) -> None:
    item = finding().model_copy(
        update={"proposal_evidence": citation("proposal", "Art. 1. " + replacement)}
    )
    checked = validate_review(review(item), context())
    assert not checked.commission_findings and not checked.complete


@pytest.mark.parametrize(
    "defect",
    ["same_date", "missing_date", "incomplete", "wrong_baseline", "same_text", "other_article"],
)
def test_incorporation_is_unconfirmed_without_valid_dated_comparison(defect: str) -> None:
    ctx = context()
    item = finding()
    after = ctx.documents[-1]
    if defect == "same_date":
        after = after.model_copy(update={"draft_date": dt.date(2026, 9, 1)})
    elif defect == "missing_date":
        after = after.model_copy(update={"draft_date": None})
    elif defect == "incomplete":
        after = after.model_copy(update={"complete": False})
    elif defect == "wrong_baseline":
        ctx = ctx.model_copy(update={"comparison_sha256": "wrong"})
    elif defect == "same_text":
        after = after.model_copy(update={"text": BEFORE})
        item = item.model_copy(update={"text_evidence": citation("after", BEFORE)})
    else:
        text = AFTER.replace("Art. 1.", "Art. 11.")
        after = after.model_copy(update={"text": text})
        item = item.model_copy(update={"text_evidence": citation("after", text, "Art. 11.")})
    ctx = ctx.model_copy(update={"documents": (*ctx.documents[:-1], after)})
    checked = validate_review(review(item), ctx)
    assert checked.commission_findings[0].outcome == "accepted"
    assert checked.commission_findings[0].text_change is None
    assert checked.commission_findings[0].text_evidence is None
    assert not checked.complete


def test_real_table_requires_whole_row_and_does_not_prove_incorporation() -> None:
    path = Path(__file__).parents[1] / "fixtures" / "commission_reviews" / "726415.json"
    source = ReviewDocument.model_validate_json(path.read_text())
    assert commission_text_matches(source.role, source.text)
    ctx = ReviewContext(number=RCL, title="", purpose="commission_review", documents=(source,))
    row = source.text.splitlines()[3]
    item = finding().model_copy(
        update={
            "proposal_evidence": citation(source.identity, row, "Art. 188e"),
            "evidence": citation(source.identity, row, "Art. 188e"),
        }
    )
    checked = validate_review(review(item), ctx).commission_findings[0]
    assert checked.outcome == "accepted" and checked.text_change is None
    assert not checked.respondent
    partial = row.rsplit("\t", 1)[0] + "\tUwaga uwzględniona."
    item = item.model_copy(update={"evidence": citation(source.identity, partial, "Art. 188e")})
    assert validate_review(review(item), ctx).commission_findings[0].outcome == "unknown"


def test_real_minutes_report_proposal_without_inventing_a_ministry_decision() -> None:
    path = Path(__file__).parents[1] / "fixtures" / "commission_reviews" / "770344.json"
    source = ReviewDocument.model_validate_json(path.read_text())
    assert commission_text_matches(source.role, source.text)
    quote = next(line for line in source.text.splitlines() if "art. 37eba ust. 4" in line)
    evidence = citation(source.identity, quote, "art. 37eba")
    ctx = ReviewContext(number=RCL, title="", purpose="commission_review", documents=(source,))
    item = finding().model_copy(update={"proposal_evidence": evidence, "evidence": evidence})
    checked = validate_review(review(item), ctx).commission_findings[0]
    assert checked.outcome == "unknown" and checked.text_change is None
    assert checked.proposal_evidence == evidence


@pytest.mark.parametrize(
    ("role", "text", "matches"),
    [
        ("commission_minutes", "Zaproszenie na posiedzenie Komisji Prawniczej", False),
        ("commission_minutes", "PROTOKÓŁ\nz posiedzenia Komisji Prawniczej\nArt. 1.", True),
        ("commission_response", "Art. 1. Uwaga uwzględniona.", True),
        ("commission_position", "Proszę o udział w komisji.", False),
    ],
)
def test_source_content_confirms_its_role(role: DocumentRole, text: str, matches: bool) -> None:
    assert commission_text_matches(role, text) is matches


def test_truncated_paragraph_cannot_hide_qualification_in_next_sentence() -> None:
    ctx = context()
    ctx = ctx.model_copy(
        update={
            "documents": tuple(
                document(
                    "response", "commission_response", RESPONSE + " Uwzględniono tylko część uwagi."
                )
                if doc.identity == "response"
                else doc
                for doc in ctx.documents
            )
        }
    )
    checked = validate_review(review(), ctx).commission_findings[0]
    assert checked.outcome == "unknown" and checked.text_change is None


def test_other_review_purposes_cannot_smuggle_commission_findings() -> None:
    ctx = context().model_copy(update={"purpose": "consultation_review"})
    answer = review().model_copy(update={"purpose": "consultation_review"})
    checked = validate_review(answer, ctx)
    assert not checked.commission_findings
