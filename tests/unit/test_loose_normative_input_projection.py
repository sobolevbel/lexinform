import pytest

from lexinform.document_input import (
    amended_input_projection,
    append_self_amendments,
    loose_normative_input_projection,
)
from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.prepared_input import prepared_text_input
from lexinform.sections import PAGE_BREAK, trim_print


def test_loose_annex_before_justification_preserves_own_source_offsets() -> None:
    head = "USTAWA\nArt. 1. Termin 30 dni.\n"
    tail = "UZASADNIENIE\nTermin 30 dni."
    annex = DocumentPart(url="annex", role="normative_annex", text="Załącznik nr 1\nTermin 30 dni.")
    main = DocumentPart(url="main", role="bill", text=head + tail)
    support = DocumentPart(url="support", role="osr", text=annex.text)
    document = TextDocument(
        url="main", kind="rcl", normative_urls=("annex",), extra_urls=("support",)
    )
    selected = DocumentSelection(parts=(main, annex, support))
    assembled = PAGE_BREAK.join((head, annex.text, tail, support.text))

    projection = loose_normative_input_projection(document, selected, assembled)

    assert projection is not None, "the loose parts reproduce the emitted assembly"
    assert [segment.source_start for segment in projection.segments] == [
        0,
        None,
        len(main.text) + 1,
        None,
        len(head),
        None,
        len(main.text) + len(annex.text) + 2,
    ]
    assert all(
        assembled[segment.start : segment.end] == PAGE_BREAK
        for segment in projection.segments
        if segment.source_start is None
    )


@pytest.mark.parametrize("mismatch", ["archive", "relation", "text"])
def test_loose_annex_map_requires_the_actual_parts_and_assembly(mismatch: str) -> None:
    document = TextDocument(url="main", kind="rcl", normative_urls=("annex",))
    main = DocumentPart(url="main", role="bill", text="USTAWA\nArt. 1.")
    annex = DocumentPart(url="annex", role="normative_annex", text="Załącznik nr 1")
    assembled = PAGE_BREAK.join((main.text, annex.text, ""))
    if mismatch == "archive":
        annex = annex.model_copy(update={"members": ("annex.pdf",)})
    elif mismatch == "relation":
        annex = annex.model_copy(update={"url": "another-annex"})
    else:
        assembled += "edited"

    assert (
        loose_normative_input_projection(
            document, DocumentSelection(parts=(main, annex)), assembled
        )
        is None
    )


def test_mixed_loose_annex_and_amendments_use_their_own_ordered_offsets() -> None:
    text = "Termin 30 dni."
    head = "USTAWA\nArt. 1. " + text + "\n"
    tail = "UZASADNIENIE\n" + text
    main = DocumentPart(url="main", role="bill", text=head + tail)
    annex = DocumentPart(url="annex", role="normative_annex", text=text)
    first = DocumentPart(url="first", role="self_amendment", text=text)
    second = DocumentPart(url="second", role="self_amendment", text=text)
    support = DocumentPart(url="support", role="osr", text=text)
    selected = DocumentSelection(parts=(main, annex, first, second, support))
    document = TextDocument(
        url="main",
        kind="rcl",
        normative_urls=("annex",),
        amendment_urls=("first", "second"),
        extra_urls=("support",),
    )
    base_text = PAGE_BREAK.join((head, annex.text, tail, support.text))
    assembly = loose_normative_input_projection(document, selected, base_text, with_amendments=True)
    assert assembly is not None, "the actual normative splice identifies the mixed base"
    assert [segment.source_start for segment in assembly.segments] == [
        0,
        None,
        len(main.text) + 1,
        None,
        len(head),
        None,
        len(main.text) + len(annex.text) + len(first.text) + len(second.text) + 4,
    ]
    trimmed = trim_print(base_text)
    assert trimmed.projection is not None, "trimming records the ordered base ranges"
    final_text = append_self_amendments(trimmed.text, (first, second))
    assert amended_input_projection(selected, trimmed.projection, final_text) is None

    projection = amended_input_projection(
        selected, trimmed.projection, final_text, assembly=assembly
    )

    assert projection is not None, "the amendment append preserves the observed splice"
    manifest = prepared_text_input(selected, final_text, projection=projection)
    assert manifest.mapping == "exact"
    assert [
        (segment.part_index, segment.source_start, segment.source_end)
        for segment in manifest.segments
        if segment.part_index in (2, 3)
    ] == [(2, 0, len(first.text)), (3, 0, len(second.text))]
    generated = "".join(
        final_text[segment.start : segment.end]
        for segment in manifest.segments
        if segment.origin == "generated"
    )
    assert "AUTOPOPRAWKI:" in generated
    assert "Źródło: first" in generated and "Źródło: second" in generated
    assert PAGE_BREAK in generated
    assert loose_normative_input_projection(document, selected, base_text) is None
    assert (
        loose_normative_input_projection(
            document.model_copy(update={"amendment_urls": ("second", "first")}),
            selected,
            base_text,
            with_amendments=True,
        )
        is None
    )
