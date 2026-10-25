import hashlib

from lexinform.document_selection import bill_selection, selection_for_input
from lexinform.models import DocumentPart, DocumentSelection
from lexinform.sections import PAGE_BREAK, excerpts, trim_print


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
    assert [(span.start, span.end, span.losses) for span in spans] == [(0, len(bill), ())]


def test_generated_omission_marker_is_not_a_source_range() -> None:
    bill, source = coverage_case()

    prepared = trim_print(source)

    assert prepared.projection is not None, (
        "trim_print records the origins of every emitted segment"
    )
    copied = [
        segment for segment in prepared.projection.segments if segment.source_start is not None
    ]
    generated = [
        segment for segment in prepared.projection.segments if segment.source_start is None
    ]
    assert [(segment.source_start, segment.end - segment.start) for segment in copied] == [
        (0, len(bill))
    ]
    assert len(generated) == 1
    assert "[pominięto:" in prepared.text[generated[0].start : generated[0].end]


def test_a_matching_fragment_elsewhere_does_not_prove_the_source_range() -> None:
    bill, source = coverage_case()
    selection = bill_selection(
        DocumentSelection(
            parts=(
                DocumentPart(
                    role="bill",
                    text=source,
                    text_end=len(source),
                    text_sha256=hashlib.sha256(source.encode()).hexdigest(),
                ),
            )
        ),
        source_basis=(),
    )
    prepared = "UNTRACED PREFIX\n" + trim_print(source).text + "\nUNTRACED SUFFIX"

    result = selection_for_input(selection, prepared)

    spans = [span for span in result.coverage if span.stage == "analysis"]
    assert bill in prepared
    assert [(span.start, span.end) for span in spans] == [(0, 0)]
    assert spans[0].losses == ("input coverage unknown",)


def test_one_copy_does_not_supply_ranges_for_two_identical_physical_parts() -> None:
    bill, _ = coverage_case()
    selection = DocumentSelection(parts=(DocumentPart(role="bill", text=bill),) * 2)

    result = selection_for_input(selection, bill)

    assert [(span.part_index, span.start, span.end, span.losses) for span in result.coverage] == [
        (0, 0, 0, ("input coverage unknown",)),
        (1, 0, 0, ("input coverage unknown",)),
    ]


def test_complete_ordered_copies_keep_their_own_part_coordinates() -> None:
    bill, _ = coverage_case()
    selection = DocumentSelection(parts=(DocumentPart(role="bill", text=bill),) * 2)

    result = selection_for_input(selection, PAGE_BREAK.join((bill, bill)))

    assert [(span.part_index, span.start, span.end, span.losses) for span in result.coverage] == [
        (0, 0, len(bill), ()),
        (1, 0, len(bill), ()),
    ]


def test_table_and_inline_note_text_keep_offsets_before_the_removed_appendix() -> None:
    bill, source = coverage_case()
    table = "\nRodzaj\tKwota\ncudzoziemiec\t500 zł [przypis 2: Dotyczy także rodziny.]\n"
    body = bill + table
    source = source.replace(bill, body, 1)
    selected = DocumentSelection(parts=(DocumentPart(role="bill", text=source),))
    prepared = trim_print(source)

    result = selection_for_input(selected, prepared.text)

    spans = [span for span in result.coverage if span.stage == "analysis"]
    assert [(span.start, span.end) for span in spans] == [(0, len(body))]
    assert "500 zł [przypis 2: Dotyczy także rodziny.]" in prepared.text


def test_untraced_multiple_excerpts_do_not_invent_a_contiguous_source_range() -> None:
    _, source = coverage_case()
    selected = DocumentSelection(parts=(DocumentPart(role="bill", text=source),))
    prepared = excerpts(
        trim_print(source).text, [(400, 430)], head_chars=50, window=20, max_chars=200
    )

    result = selection_for_input(selected, prepared)

    assert "[...]" in prepared
    spans = [span for span in result.coverage if span.stage == "analysis"]
    assert [(span.start, span.end) for span in spans] == [(0, 0)]
    assert "input coverage unknown" in spans[0].losses
