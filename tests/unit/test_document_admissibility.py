import pytest

from lexinform.document_admissibility import analysis_admission, prefilter_admission
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
