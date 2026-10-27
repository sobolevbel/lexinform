import pytest

from lexinform.document_admissibility import (
    analysis_admission,
    prefilter_admission,
    review_admission,
)
from lexinform.document_fingerprints import text_digest
from lexinform.models import DocumentSelection

BILL = (
    "USTAWA\nz dnia 1 stycznia 2026 r.\no cudzoziemcach\nArt. 1. Cudzoziemiec otrzymuje 200 zł.\n"
    * 20
)


@pytest.mark.parametrize("state", ["waiting", "ambiguous", "unsupported"])
def test_blocked_selection_precedes_even_a_dense_bill_or_missing_body(state: str) -> None:
    selection = DocumentSelection.model_validate({"state": state})
    for text in (None, BILL):
        assert prefilter_admission(selection, text, pages=1, min_chars=200) == state
        assert analysis_admission(selection, text, pages=1, min_chars=200) == state


def test_legacy_unverified_bill_text_remains_eligible_for_both_operations() -> None:
    selection = DocumentSelection()
    assert prefilter_admission(selection, BILL, pages=1, min_chars=200) == "text"
    assert analysis_admission(selection, BILL, pages=1, min_chars=200) == "text"


@pytest.mark.parametrize("pages", [0, 1, 20])
def test_missing_text_keeps_the_existing_prefilter_and_scan_attempt_paths(pages: int) -> None:
    selection = DocumentSelection()
    assert prefilter_admission(selection, None, pages=pages, min_chars=200) == "no_text"
    assert analysis_admission(selection, None, pages=pages, min_chars=200) == "scan"


def test_thin_pdf_text_is_offered_as_pages_instead_of_keyword_search() -> None:
    selection = DocumentSelection()
    thin = "Pismo przewodnie " * 30
    assert prefilter_admission(selection, thin, pages=100, min_chars=200) == "no_document"
    assert analysis_admission(selection, thin, pages=100, min_chars=200) == "scan"


def test_nonempty_word_fallback_is_deliberately_different_from_prefilter() -> None:
    selection = DocumentSelection()
    thin = "Krótki tekst dokumentu"
    assert prefilter_admission(selection, thin, pages=0, min_chars=200) == "no_document"
    assert analysis_admission(selection, thin, pages=0, min_chars=200) == "text"


def test_empty_text_without_pages_still_uses_the_scan_loader_fallback() -> None:
    selection = DocumentSelection()
    assert prefilter_admission(selection, "", pages=0, min_chars=200) == "no_document"
    assert analysis_admission(selection, "", pages=0, min_chars=200) == "scan"


def test_a_dense_appendix_is_not_analysed_as_the_bill() -> None:
    selection = DocumentSelection()
    text = "RAPORT Z KONSULTACJI\n" + "Omówienie przebiegu konsultacji i zgłoszonych uwag. " * 80
    assert analysis_admission(selection, text, pages=0, min_chars=200) == "metadata_only"


def test_osr_remains_readable_instead_of_being_treated_as_an_unrelated_appendix() -> None:
    selection = DocumentSelection()
    text = "OCENA SKUTKÓW REGULACJI\n" + "Wpływ regulacji na sektor finansów publicznych. " * 80
    assert analysis_admission(selection, text, pages=0, min_chars=200) == "text"


def test_review_accepts_a_dense_unverified_response_without_a_bill_heading() -> None:
    text = "Pkt 1. Uwaga uwzględniona; termin wynosi 30 dni. " * 30
    assert (
        review_admission(
            DocumentSelection(), text, pages=1, min_chars=200, oversize=False, role="response"
        )
        == "complete"
    )


def test_review_expected_digest_is_normalized_text_identity() -> None:
    assert (
        review_admission(
            DocumentSelection(),
            BILL,
            pages=1,
            min_chars=200,
            oversize=False,
            role="project_text",
            expected_digest=text_digest(BILL.replace(" ", "  ")),
        )
        == "complete"
    )
    assert (
        review_admission(
            DocumentSelection(),
            BILL.replace("200 zł", "300 zł"),
            pages=1,
            min_chars=200,
            oversize=False,
            role="project_text",
            expected_digest=text_digest(BILL),
        )
        == "digest_mismatch"
    )


def test_review_role_mismatch_is_rejected_before_completeness() -> None:
    assert (
        review_admission(
            DocumentSelection(),
            BILL,
            pages=1,
            min_chars=200,
            oversize=False,
            role="commission_minutes",
        )
        == "role_mismatch"
    )


@pytest.mark.parametrize("state", ["unverified", "waiting", "ambiguous", "unsupported"])
def test_review_preserves_its_own_selection_completeness_contract(state: str) -> None:
    selection = DocumentSelection.model_validate({"state": state})
    assert review_admission(
        selection, BILL, pages=1, min_chars=200, oversize=False, role="project_text"
    ) == ("partial" if state in ("ambiguous", "unsupported") else "complete")


@pytest.mark.parametrize("pages", [0, 20])
def test_review_keeps_thin_text_as_partial_instead_of_using_analysis_fallback(pages: int) -> None:
    assert (
        review_admission(
            DocumentSelection(),
            "Krótki tekst",
            pages=pages,
            min_chars=200,
            oversize=False,
            role="response",
        )
        == "partial"
    )


@pytest.mark.parametrize("oversize", [False, True])
def test_review_incomplete_inputs_are_kept_as_partial(oversize: bool) -> None:
    selection = DocumentSelection() if oversize else DocumentSelection(rejected=("part unread",))
    assert (
        review_admission(
            selection, BILL, pages=1, min_chars=200, oversize=oversize, role="project_text"
        )
        == "partial"
    )
