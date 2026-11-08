from typing import Literal

import pytest

from lexinform.document_candidates import (
    AmbiguousCandidates,
    CandidateSet,
    DocumentCandidate,
    SelectedCandidate,
    select_candidate_versions,
    select_document_parts,
)
from lexinform.document_versions import comparable_versions, compare_version_decision
from lexinform.models import (
    DocumentPart,
    DocumentSelection,
    ExtractedDocument,
    ExtractedParts,
    TextDocument,
)
from lexinform.models.candidate_trace import VersionDecisionEvidence
from lexinform.sections import PAGE_BREAK
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway
from tests.unit.test_physical_extraction import archive, docx, extractor

BILL = (
    "USTAWA\nz dnia 1 stycznia 2026 r.\no cudzoziemcach\nArt. 1. Cudzoziemiec otrzymuje 200 zł.\n"
)
WHOLE_ACT = PAGE_BREAK.join(
    (
        BILL,
        "Art. 2. Ustawa wchodzi w życie po upływie 30 dni od dnia ogłoszenia.",
        "UZASADNIENIE\nProjekt zmienia zasady pobytu.",
    )
)
COVERAGE_LOSS = "text page coverage differs from the PDF page count"


def test_archive_composition_keeps_annexes_and_support_from_the_selected_occurrence() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(
            DocumentPart(text=BILL, members=("inner.zip", "projekt.pdf")),
            DocumentPart(text="Załącznik do ustawy\nWłasny", members=("inner.zip", "own.pdf")),
            DocumentPart(text="UZASADNIENIE\nWłasne", members=("inner.zip", "uzasadnienie-z.pdf")),
            DocumentPart(text="Załącznik do ustawy\nObcy", members=("inner.zip", "foreign.pdf")),
            DocumentPart(text="UZASADNIENIE\nObce", members=("inner.zip", "uzasadnienie-a.pdf")),
        ),
        part_archive_ordinals=((0, 0), (0, 1), (0, 2), (1, 0), (1, 1)),
    )

    selected = select_document_parts(physical, max_part_chars=None)

    assert [part.members[-1] for part in selected.selection.parts] == [
        "projekt.pdf",
        "own.pdf",
        "uzasadnienie-z.pdf",
    ]
    assert len(selected.selection.observed_parts) == 5


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


@pytest.mark.parametrize(("amount", "state"), [("200", "unverified"), ("300", "ambiguous")])
def test_equal_archive_bill_bodies_do_not_hide_distinct_annexes(amount: str, state: str) -> None:
    annex = "Załącznik do ustawy\nWykaz\n200 zł"
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(
            DocumentPart(text=BILL, members=("a.zip", "projekt.pdf")),
            DocumentPart(text=annex, members=("a.zip", "annex.pdf")),
            DocumentPart(text=BILL, members=("b.zip", "projekt.pdf")),
            DocumentPart(text=annex.replace("200", amount), members=("b.zip", "annex.pdf")),
        ),
        part_archive_ordinals=((0, 0), (0, 1), (1, 0), (1, 1)),
    )

    result = select_document_parts(physical, max_part_chars=None)

    assert result.selection.state == state
    assert len(result.selection.parts) == (4 if state == "ambiguous" else 2)
    assert ("draft_version_conflict" in result.selection.limitations) == (state == "ambiguous")
    assert bool(result.text) == (state != "ambiguous")


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


@pytest.mark.parametrize("physical_parts", [False, True])
def test_conflicting_file_versions_keep_compared_and_observed_parts(physical_parts: bool) -> None:
    first = DocumentPart(text=BILL, members=("one.pdf",))
    second = DocumentPart(text=BILL.replace("200 zł", "300 zł"), members=("two.pdf",))
    extra = DocumentPart(text="Physical appendix", members=("other.pdf",))
    observed = (first, extra) if physical_parts else (first,)
    candidates = CandidateSet(
        (
            DocumentCandidate("opinion", "UZASADNIENIE"),
            DocumentCandidate("one", first.text, (first,), observed),
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
    assert choice.selection.observed_parts == ((first, extra, second) if physical_parts else ())


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


@pytest.mark.parametrize(
    ("text", "pages", "losses", "expected", "role"),
    [
        (WHOLE_ACT, 8, (COVERAGE_LOSS,), (), "bill"),
        (WHOLE_ACT, 8, (COVERAGE_LOSS, "missing note"), ("missing note",), "bill"),
        (WHOLE_ACT, 1, (COVERAGE_LOSS,), (COVERAGE_LOSS,), "bill"),
        (
            WHOLE_ACT.split(PAGE_BREAK, 1)[0],
            8,
            (COVERAGE_LOSS,),
            (COVERAGE_LOSS,),
            "bill",
        ),
        ("", 8, ("no extracted text", COVERAGE_LOSS), ("no extracted text",), "unknown"),
        (
            "",
            0,
            ("no extracted text", COVERAGE_LOSS),
            ("no extracted text", COVERAGE_LOSS),
            "unknown",
        ),
    ],
    ids=[
        "through-reasons",
        "other-loss-kept",
        "extra-chunks",
        "incomplete-act",
        "blank",
        "unknown-count",
    ],
)
def test_coverage_projection_preserves_legacy_selected_json_and_physical_snapshot(
    text: str,
    pages: int,
    losses: tuple[str, ...],
    expected: tuple[str, ...],
    role: Literal["bill", "unknown"],
) -> None:
    part = DocumentPart(text=text, pages=pages, losses=losses)
    physical = ExtractedParts(input_sha256="a" * 64, parts=(part,))
    before = physical.model_dump_json()
    expected_document = ExtractedDocument(
        text=text,
        selection=DocumentSelection(
            parts=(part.model_copy(update={"role": role, "losses": expected}),)
        ),
    )
    selected = select_document_parts(physical, max_part_chars=400000)
    assert selected.model_dump_json() == expected_document.model_dump_json()
    assert physical.model_dump_json() == before


def test_package_coverage_projection_preserves_legacy_completeness_and_observed_parts() -> None:
    part = DocumentPart(text=WHOLE_ACT, pages=8, losses=(COVERAGE_LOSS,), members=("projekt.pdf",))
    physical = ExtractedParts(input_sha256="a" * 64, parts=(part,))
    before = physical.model_dump_json()
    projected = part.model_copy(update={"role": "bill", "losses": ()})
    expected = ExtractedDocument(
        text=WHOLE_ACT,
        selection=DocumentSelection(
            parts=(projected,),
            observed_parts=(projected,),
            role=("bill heading",),
            completeness=("all selected members extracted",),
        ),
    )
    selected = select_document_parts(physical, max_part_chars=400000)
    assert selected.model_dump_json() == expected.model_dump_json()
    assert physical.model_dump_json() == before


def test_version_comparison_reports_date_filter_without_changing_existing_api() -> None:
    texts = [
        "Projekt z dnia 1 marca 2026 r.\n" + BILL,
        "Projekt z dnia 2 marca 2026 r.\n" + BILL.replace("200 zł", "300 zł"),
    ]

    comparison = compare_version_decision(texts)

    assert comparison.matching_indices == comparable_versions(texts) == (1,)
    assert comparison.retained_indices == (1,)
    assert comparison.date_filter is not None, "the dates excluded the first candidate"
    assert tuple(date.isoformat() for date in comparison.date_filter) == (
        "2026-03-01",
        "2026-03-02",
    )


def test_outer_comparison_records_matching_archive_and_standalone_versions() -> None:
    packed_url, loose_url = "https://api.test/a.zip", "https://api.test/b.docx"
    body = docx(BILL + "Art. 2. Przepis stosuje się w postępowaniu. " * 12)
    gateway = FakeSejmGateway(files={packed_url: archive({"projekt.docx": body}), loose_url: body})
    loader = TextLoader({"api.test": gateway.download}, extractor(), max_bytes=100_000)
    document = TextDocument(
        url=packed_url,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=loose_url, kind="rcl"),),
    )

    selected, file = loader.select_document(document)

    assert selected.url == packed_url
    trace = file.candidate_trace
    assert trace is not None, "the bounded outer trace remains available"
    assert trace.reason == "version_choice"
    assert file.selection.parts[0].members == ("projekt.docx",)
    assert trace.version_decision == VersionDecisionEvidence(
        compared_indices=(0, 1),
        retained_indices=(0, 1),
        outcome="matching_policy_identity",
    )


@pytest.mark.parametrize(("available", "result"), [(True, "selected"), (False, "waiting")])
def test_outer_comparison_reads_declared_annex_before_choosing(
    available: bool, result: str
) -> None:
    packed_url, loose_url, annex_url = (
        "https://api.test/a.zip",
        "https://api.test/b.docx",
        "https://api.test/annex.docx",
    )
    body = docx(BILL + "Art. 2. Przepis stosuje się w postępowaniu. " * 12)
    annex = docx("Załącznik do ustawy " + "Wykaz opłat wynosi 200 zł. " * 20)
    files = {
        packed_url: archive({"projekt.docx": body, "załącznik do ustawy.docx": annex}),
        loose_url: body,
    }
    if available:
        files[annex_url] = annex
    gateway = FakeSejmGateway(files=files)
    loader = TextLoader({"api.test": gateway.download}, extractor(), max_bytes=100_000)
    document = TextDocument(
        url=packed_url,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=loose_url, kind="rcl", normative_urls=(annex_url,)),),
    )

    _, file = loader.select_document(document)

    trace = file.candidate_trace
    assert trace is not None and trace.result == result
    assert f"download:{annex_url}" in gateway.calls
    if available:
        assert [part.role for part in file.selection.parts] == ["bill", "normative_annex"]
        assert trace.version_decision is not None
        inputs = trace.version_decision.package_inputs
        assert inputs is not None and len(inputs) == 2
        assert inputs[0].annex_text_sha256s == inputs[1].annex_text_sha256s
        assert inputs[0].body_sha256 == inputs[1].body_sha256
    else:
        assert file.text is None and trace.version_decision is None
        assert trace.dispositions[1].reason == "normative_part_read_incomplete"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("same", "selected"), ("missing", "ambiguous"), ("unavailable", "waiting")],
)
def test_outer_comparison_keeps_self_amendments_separate(mode: str, expected: str) -> None:
    first, second, proposal = (
        "https://api.test/first.docx",
        "https://api.test/second.docx",
        "https://api.test/proposal.docx",
    )
    body = docx(BILL + "Art. 2. Przepis stosuje się w postępowaniu. " * 12)
    proposal_text = "AUTOPOPRAWKA " + "Kwota zmienia się na 300 zł. " * 20
    files = {first: body, second: body}
    if mode != "unavailable":
        files[proposal] = docx(proposal_text)
    gateway = FakeSejmGateway(files=files)
    loader = TextLoader({"api.test": gateway.download}, extractor(), max_bytes=100_000)
    document = TextDocument(
        url=first,
        kind="rcl",
        compare_versions=True,
        amendment_urls=(proposal,) if mode == "same" else (),
        alternatives=(TextDocument(url=second, kind="rcl", amendment_urls=(proposal,)),),
    )

    _, file = loader.select_document(document)

    trace = file.candidate_trace
    assert trace is not None and trace.result == expected
    if mode == "same":
        assert file.text is not None and proposal_text not in file.text
        assert [part.role for part in file.selection.parts] == ["bill", "self_amendment"]
    elif mode == "unavailable":
        assert trace.dispositions[1].reason == "normative_part_read_incomplete"
