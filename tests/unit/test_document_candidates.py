from lexinform.document_candidates import (
    AmbiguousCandidates,
    CandidateSet,
    DocumentCandidate,
    SelectedCandidate,
    select_candidate_versions,
    select_document_parts,
)
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


def test_file_selection_keeps_input_indices_after_non_bill_candidates() -> None:
    bill = DocumentPart(text=BILL)
    physical = DocumentPart(text="Physical appendix")
    candidates = CandidateSet(
        (
            DocumentCandidate("scan", None),
            DocumentCandidate("reasons", "UZASADNIENIE"),
            DocumentCandidate("bill", BILL, (bill,), (bill, physical)),
        )
    )
    choice = select_candidate_versions(candidates)
    assert isinstance(choice, SelectedCandidate), (
        "the policy selects the sole header-classified bill"
    )
    assert choice.index == 2 and choice.observed_parts == (bill, physical)


def test_equal_file_versions_preserve_official_input_priority() -> None:
    first = DocumentPart(text=BILL, members=("z.pdf",))
    second = DocumentPart(text=BILL, members=("a.pdf",))
    candidates = CandidateSet(
        (
            DocumentCandidate("https://api.test/z.pdf", BILL, (first,)),
            DocumentCandidate("https://api.test/a.pdf", BILL, (second,)),
        )
    )
    choice = select_candidate_versions(candidates)
    assert isinstance(choice, SelectedCandidate), "equal bill versions retain the input priority"
    assert choice.index == 0 and choice.observed_parts == (first, second)


def test_conflicting_file_versions_keep_only_bill_members_and_source_reasons() -> None:
    first = DocumentPart(text=BILL, members=("one.pdf",))
    second = DocumentPart(text=BILL.replace("200 zł", "300 zł"), members=("two.pdf",))
    candidates = CandidateSet(
        (
            DocumentCandidate("opinion", "UZASADNIENIE"),
            DocumentCandidate("one", first.text, (first,)),
            DocumentCandidate("two", second.text, (second,)),
        )
    )
    choice = select_candidate_versions(candidates)
    assert isinstance(choice, AmbiguousCandidates), (
        "different normative amounts conflict under the version policy"
    )
    assert choice.selection.state == "ambiguous" and choice.selection.parts == (first, second)
    assert choice.selection.rejected == (
        "one: conflicting bill version",
        "two: conflicting bill version",
    )
    assert not choice.selection.observed_parts


def test_no_bill_body_leaves_the_loader_fallback_available() -> None:
    candidates = CandidateSet(
        (DocumentCandidate("scan", None), DocumentCandidate("reasons", "UZASADNIENIE"))
    )
    assert select_candidate_versions(candidates) is None


def test_physical_parts_share_body_classification_without_trusting_a_filename_role() -> None:
    labelled = DocumentPart(text="UZASADNIENIE", members=("projekt.pdf",), role="bill")
    bill = DocumentPart(text=BILL, members=("other.pdf",))
    candidates = CandidateSet.from_parts((labelled, bill))
    assert candidates.bill_indices() == (1,)
    assert candidates.entries[1].parts == candidates.entries[1].observed_parts == (bill,)
