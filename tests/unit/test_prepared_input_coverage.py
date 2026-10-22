import hashlib

import pytest

from lexinform.document_selection import bill_selection, selection_for_input
from lexinform.models import DocumentPart, DocumentSelection
from lexinform.sections import PAGE_BREAK, trim_print


def coverage_case() -> tuple[str, str]:
    heading = (
        "Projekt\nUSTAWA\nz dnia 1 stycznia 2026 r.\n"
        "o zmianie ustawy o cudzoziemcach\nArt. 1. Termin wynosi 30 dni.\n"
        "Art. 2. Ustawa wchodzi w życie po upływie 14 dni.\n"
    )
    bill = heading + "x" * (512 - len(heading))
    report = "Raport z konsultacji publicznych\n" + "c" * 618
    return bill, PAGE_BREAK.join((bill, report))


def test_coverage_audit_case_keeps_the_bill_and_drops_the_consultation_report() -> None:
    bill, source = coverage_case()

    prepared = trim_print(source)

    assert len(source) == 1164
    assert len(prepared.text) == 559
    assert prepared.text.startswith(bill)
    assert "c" * 100 not in prepared.text
    assert [section.name for section in prepared.dropped] == ["raport z konsultacji"]


@pytest.mark.xfail(
    strict=True, reason="DA5: analysis coverage includes trimmed consultation report"
)
def test_analysis_coverage_excludes_material_removed_from_prepared_input() -> None:
    bill, source = coverage_case()
    selection = bill_selection(
        DocumentSelection(
            parts=(
                DocumentPart(
                    role="bill",
                    url="https://api.test/prints/1.pdf",
                    text=source,
                    text_sha256=hashlib.sha256(source.encode()).hexdigest(),
                    text_end=len(source),
                ),
            )
        ),
        source_basis=("official print attachment",),
    )

    result = selection_for_input(selection, trim_print(source).text)

    spans = [span for span in result.coverage if span.stage == "analysis"]
    assert spans
    assert all(span.end <= len(bill) for span in spans)
