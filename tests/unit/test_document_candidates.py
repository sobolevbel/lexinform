from lexinform.document_candidates import select_document_parts
from lexinform.models import DocumentPart, ExtractedParts

BILL = (
    "USTAWA\nz dnia 1 stycznia 2026 r.\no cudzoziemcach\nArt. 1. Cudzoziemiec otrzymuje 200 zł.\n"
)


def test_selection_preserves_the_physical_snapshot_for_other_consumers() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(DocumentPart(text=BILL, members=("bundle", "projekt.docx")),),
        rejected=("unreadable member",),
    )
    before = physical.model_dump_json()
    selected = select_document_parts(physical, max_part_chars=400_000)
    repeated = select_document_parts(physical, max_part_chars=400_000)
    assert selected.selection.parts[0].role == "bill"
    assert selected.text == BILL and selected.selection.rejected == ("unreadable member",)
    assert repeated == selected
    assert physical.model_dump_json() == before


def test_unrecognised_member_limit_is_an_explicit_selection_input() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(DocumentPart(text="x" * 100, members=("bundle", "projekt.docx")),),
    )
    limited = select_document_parts(physical, max_part_chars=50)
    unlimited = select_document_parts(physical, max_part_chars=None)
    assert limited.selection.state == "unsupported"
    assert any("unrecognised long document" in reason for reason in limited.selection.rejected)
    assert unlimited.text == "x" * 100 and unlimited.selection.parts[0].role == "bill"
    assert not physical.rejected


def test_confirmed_versions_with_different_amounts_remain_ambiguous() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(
            DocumentPart(text=BILL, members=("bundle", "projekt.docx")),
            DocumentPart(
                text=BILL.replace("200 zł", "300 zł"), members=("bundle", "projekt2.docx")
            ),
        ),
    )
    result = select_document_parts(physical, max_part_chars=400_000)
    assert result.selection.state == "ambiguous" and not result.text
    assert len(result.selection.observed_parts) == len(result.selection.parts) == 2
