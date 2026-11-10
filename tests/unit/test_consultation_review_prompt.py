import json
from datetime import date
from hashlib import sha256

from lexinform.adapters.review_prompts import review_prompt
from lexinform.models import DocumentReview, ReviewContext, ReviewDocument
from lexinform.models.reviews import ConsultationRow, EvidenceCitation, validate_review
from tests.scenario.test_document_reviews import context, finding, review


def test_consultation_prompt_keeps_duplicate_source_metadata_without_repeating_its_text() -> None:
    original = ReviewDocument(
        identity="rcl/780621",
        url="https://rcl.test/780621",
        role="response",
        text="Uwaga nieuwzględniona.",
        sha256="a" * 64,
        normalized_sha256="b" * 64,
        file_sha256="c" * 64,
    )
    alias = original.model_copy(
        update={"identity": "rcl/780641", "url": "https://rcl.test/780641", "author": "Minister"}
    )
    independent = alias.model_copy(update={"identity": "rcl/42", "file_sha256": "d" * 64})
    context = ReviewContext(
        number="RCL/12398102",
        title="Projekt ustawy",
        purpose="consultation_review",
        documents=(original, alias, independent),
    )

    first, copied, other = json.loads(review_prompt(context))["documents"]

    assert first["text"] == other["text"] == original.text
    assert "text" not in copied
    assert (copied["identity"], copied["same_text_as"]) == (alias.identity, original.identity)
    assert (copied["url"], copied["author"]) == (alias.url, alias.author)
    assert context.documents[1].text == original.text


def test_word_row_citation_restores_the_source_and_rejects_unknown_coordinates() -> None:
    text = "Nagłówek\n\tStowarzyszenie\tOpłata 200 PLN\tUwaga uwzględniona.\nKoniec"
    locator = "word-xml:word/document.xml:0/1/2"
    start = text.index("\n") + 1
    end = text.rindex("\n")
    digest = sha256(text.encode()).hexdigest()
    source = ReviewDocument(
        identity="rcl/780621",
        url="https://rcl.test/780621",
        role="response",
        text=text,
        sha256=digest,
        normalized_sha256=digest,
        consultation_rows=(ConsultationRow(locator=locator, label="93", start=start, end=end),),
    )
    context = ReviewContext(
        number="RCL/12398102",
        title="Projekt ustawy",
        purpose="consultation_review",
        documents=(source,),
    )
    packed = json.loads(review_prompt(context))["documents"][0]
    assert "text" not in packed
    assert "".join(piece["text"] for piece in packed["segments"]) == text
    assert packed["segments"][1]["locator"] == locator
    review = DocumentReview.model_validate(
        {
            "purpose": "consultation_review",
            "summary": "",
            "complete": True,
            "unresolved": [],
            "effective_rules": [],
            "consultation_findings": [
                {
                    "proposal": "Оплата 200 PLN",
                    "respondent": "",
                    "outcome": "accepted",
                    "reason": "",
                    "affected_groups": [],
                    "evidence": {"document_id": source.identity, "locator": locator, "quote": ""},
                    "text_change": None,
                    "text_evidence": None,
                },
            ],
        }
    )
    (finding,) = validate_review(review, context).consultation_findings
    assert finding.outcome == "accepted"
    assert finding.evidence is not None, "a known row is recovered from its original document"
    assert finding.evidence.quote == text[start:end]

    other = context.model_copy(
        update={"documents": (source.model_copy(update={"consultation_rows": ()}),)}
    )
    (missing,) = validate_review(review, other).consultation_findings
    assert missing.evidence is None and missing.outcome == "unknown"


def test_consultation_incorporation_requires_two_dated_versions_of_the_same_provision() -> None:
    original = context()
    before_text = "Art. 3. Wniosek składa się w terminie 14 dni."
    after_text = "Art. 3. Wniosek składa się w terminie 30 dni."
    before = (
        context(before_text, role="comparison_text")
        .documents[0]
        .model_copy(update={"identity": "before", "draft_date": date(2026, 1, 1)})
    )
    after = (
        context(after_text, role="project_text")
        .documents[0]
        .model_copy(update={"identity": "after", "draft_date": date(2026, 2, 1)})
    )
    combined = original.model_copy(
        update={
            "documents": (*original.documents, before, after),
            "comparison_sha256": before.normalized_sha256,
        }
    )
    proposed = finding().model_copy(
        update={
            "text_change": "Срок увеличили с 14 до 30 дней.",
            "text_evidence": EvidenceCitation(
                document_id="after", locator="Art. 3", quote=after_text
            ),
        }
    )

    (unproven,) = validate_review(review(proposed), combined).consultation_findings
    assert unproven.text_change is None
    proven = proposed.model_copy(
        update={
            "comparison_evidence": EvidenceCitation(
                document_id="before", locator="Art. 3", quote=before_text
            )
        }
    )
    (confirmed,) = validate_review(review(proven), combined).consultation_findings
    assert confirmed.text_change == proposed.text_change
    assert confirmed.comparison_evidence == proven.comparison_evidence


def test_official_justification_does_not_claim_the_proposal_was_incorporated() -> None:
    quote = "Pkt 1. Uwaga częściowo zasadna. Projektodawca przeanalizuje uwagę."
    candidate = finding(quote=quote, outcome="partial").model_copy(
        update={"reason": "Ведомство обещало изучить замечание."}
    )
    (confirmed,) = validate_review(review(candidate), context(quote)).consultation_findings
    assert confirmed.response_label == "partly_justified"
    assert confirmed.outcome == "unknown"
    assert confirmed.reason == candidate.reason
    assert confirmed.text_change is None


def test_official_unjustified_label_supports_rejection() -> None:
    quote = "Pkt 1. Uwaga niezasadna. Projektodawca podtrzymuje stanowisko."
    candidate = finding(quote=quote, outcome="rejected")
    (confirmed,) = validate_review(review(candidate), context(quote)).consultation_findings
    assert confirmed.outcome == "rejected"
