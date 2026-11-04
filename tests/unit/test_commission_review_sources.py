import pytest

from lexinform.adapters.senat_html import parse_act
from lexinform.commission_reviews import (
    commission_text_matches,
    filed_commission_sources,
)
from lexinform.models import (
    Attachment,
    PrintInfo,
    ReviewContext,
    ReviewDocument,
    validate_review,
)
from tests.commission_cases import PROPOSAL, REPORT, citation, context, document, finding, review
from tests.conftest import SENAT_FIXTURES


def test_joint_senate_reports_and_legal_opinions_are_bound_to_their_own_act() -> None:
    act = parse_act(
        (SENAT_FIXTURES / "ustawa_2169.html").read_text(), "https://www.senat.gov.pl/act"
    )
    assert len(act.review_documents) == 2
    assert {doc.role for doc in act.review_documents} == {
        "commission_report",
        "commission_position",
    }
    report = next(doc for doc in act.review_documents if doc.role == "commission_report")
    assert "Komisji Rodziny" in report.body and "Komisji Spraw Zagranicznych" in report.body
    assert report.url.endswith("849a.pdf")
    assert not any(doc.url.endswith("/849.pdf") for doc in act.review_documents)
    consultation = parse_act(
        (SENAT_FIXTURES / "ustawa_2170.html").read_text(), "https://www.senat.gov.pl/act"
    )
    assert len(consultation.review_documents) == 1
    assert consultation.review_documents[0].role == "commission_position"


def test_other_body_cannot_supply_an_answer_or_recommendation_to_this_proposal() -> None:
    ctx = context()
    ctx = ctx.model_copy(
        update={
            "documents": tuple(
                doc.model_copy(
                    update={
                        "body": "Komisja B" if doc.identity == "response" else "Komisja A",
                        "forum": "government",
                    }
                )
                for doc in ctx.documents
            )
        }
    )
    result = validate_review(review(), ctx).commission_findings[0]
    assert result.outcome == "unknown" and result.text_change is None


def test_recommendation_never_becomes_a_chamber_decision_even_if_report_says_accepted() -> None:
    report = document("report", "commission_report", REPORT).model_copy(
        update={"forum": "senate", "body": "Komisja A"}
    )
    ctx = ReviewContext(
        number="3039", title="Projekt", purpose="commission_review", documents=(report,)
    )
    item = finding().model_copy(
        update={
            "proposal_evidence": citation("report", PROPOSAL),
            "evidence": citation("report", REPORT, "Sprawozdanie"),
            "recommendation": "Комиссия предлагает принять поправки",
            "recommendation_evidence": citation("report", REPORT, "Sprawozdanie"),
        }
    )
    result = validate_review(review(item), ctx).commission_findings[0]
    assert result.outcome == "unknown" and result.recommendation
    assert result.text_change is None


@pytest.mark.parametrize(
    "text",
    [
        "Sprawozdanie komisji. Wniosek mniejszości: komisja powinna przyjąć poprawki.",
        "Sprawozdanie komisji. Wnioski mniejszości: wnoszą o przyjęcie poprawek.",
        "Sprawozdanie. Uczestnik posiedzenia proponuje przyjęcie poprawki.",
    ],
)
def test_minority_and_participant_proposals_are_not_committee_recommendations(text: str) -> None:
    doc = document("report", "commission_report", text)
    ctx = ReviewContext(
        number="3039", title="Projekt", purpose="commission_review", documents=(doc,)
    )
    evidence = citation("report", text, "Sprawozdanie")
    item = finding().model_copy(
        update={
            "proposal_evidence": evidence,
            "recommendation": "Принять поправки",
            "recommendation_evidence": evidence,
        }
    )
    checked = validate_review(review(item), ctx).commission_findings[0]
    assert not checked.recommendation and checked.outcome == "unknown"


def test_consultation_attachments_cannot_be_renamed_into_official_senate_opinions() -> None:
    html = (SENAT_FIXTURES / "ustawa_2170.html").read_text().replace("Uwaga 2", "Opinia do ustawy")
    act = parse_act(html, "https://www.senat.gov.pl/act")
    assert len(act.review_documents) == 1
    assert "832o.pdf" in act.review_documents[0].url


def test_same_file_at_another_url_keeps_the_review_memo_key() -> None:
    doc = document("report", "commission_report", REPORT).model_copy(
        update={"body": "Komisja A", "forum": "sejm"}
    )
    ctx = ReviewContext(
        number="3039", title="Projekt", purpose="commission_review", documents=(doc,)
    )
    changed = ctx.model_copy(
        update={
            "documents": (
                doc.model_copy(
                    update={"identity": "new-url", "url": "https://example.test/new-url"}
                ),
            )
        }
    )
    assert ctx.memo_key(10) == changed.memo_key(10)
    other = ctx.model_copy(update={"documents": (doc.model_copy(update={"body": "Komisja B"}),)})
    assert ctx.memo_key(10) != other.memo_key(10)


@pytest.mark.parametrize("name", ["krmc-722570", "sejm-1010", "senate-606a"])
def test_real_reports_remain_readable_including_letter_spaced_sejm_heading(name: str) -> None:
    path = SENAT_FIXTURES.parent / "commission_reviews" / f"{name}.json"
    doc = ReviewDocument.model_validate_json(path.read_text())
    assert commission_text_matches(doc.role, doc.text)
    quote = doc.text if name != "sejm-1010" else doc.text.split("Warszawa, dnia", 1)[0]
    evidence = citation(
        doc.identity, quote, "Konkluzja" if name == "krmc-722570" else "Sprawozdanie"
    )
    item = finding().model_copy(
        update={
            "proposal_evidence": evidence,
            "text_change": None,
            "recommendation": "Комиссия рекомендует принять закон",
            "recommendation_evidence": evidence,
        }
    )
    ctx = ReviewContext(
        number="3039", title="Проект", purpose="commission_review", documents=(doc,)
    )
    result = validate_review(review(item), ctx)
    assert result.complete
    assert result.commission_findings[0].recommendation
    assert result.commission_findings[0].outcome == "unknown"


def test_filed_opinion_for_another_parent_is_not_selected() -> None:
    attachment = Attachment(
        print_number="opinion", name="opinion.pdf", url="https://api.test/opinion.pdf"
    )
    opinion = PrintInfo(
        term=10,
        number="opinion",
        title="Opinia Komisji Nowej",
        attachments=(attachment,),
        process_prints=("3040",),
    )
    parent = PrintInfo(term=10, number="3039", title="Projekt", additional_prints=(opinion,))
    assert not filed_commission_sources(parent)
