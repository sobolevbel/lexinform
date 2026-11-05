import pytest

from lexinform.document_input import loose_normative_input_projection
from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.sections import PAGE_BREAK


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
