import hashlib

import pytest

from lexinform.document_input import amended_input_projection, append_self_amendments
from lexinform.models import DocumentPart, DocumentSelection
from lexinform.sections import PAGE_BREAK, TextBudget, trim_print
from lexinform.text_projection import SourcePiece, project_pieces


def test_reordered_main_support_and_duplicate_proposals_keep_their_own_offsets() -> None:
    body = DocumentPart(role="bill", text="USTAWA\nArt. 1. Termin 30 dni.\n")
    proposal = DocumentPart(
        role="self_amendment", url="https://source.test/1", text="30 dni -> 60 dni"
    )
    support = DocumentPart(role="justification", text="UZASADNIENIE\nCudzoziemcy.")
    duplicate = proposal.model_copy(update={"url": "https://source.test/2"})
    parts = (body, proposal, support, duplicate)
    source = PAGE_BREAK.join(part.text for part in parts)
    main = trim_print(PAGE_BREAK.join((body.text, support.text)))
    assert main.projection is not None, "trim_print records the exact main text transformation"
    prepared = append_self_amendments(main.text, (proposal, duplicate))

    projection = amended_input_projection(DocumentSelection(parts=parts), main.projection, prepared)

    assert projection is not None, "the selected parts reproduce the main assembly exactly"
    assert projection.text == prepared
    assert projection.source_sha256 == hashlib.sha256(source.encode()).hexdigest()
    origins = [
        segment.source_start for segment in projection.segments if segment.source_start is not None
    ]
    support_offset = len(body.text) + len(proposal.text) + 2
    duplicate_offset = support_offset + len(support.text) + 1
    assert origins == [0, support_offset, len(body.text) + 1, duplicate_offset]
    for segment in projection.segments:
        text = prepared[segment.start : segment.end]
        if segment.source_start is None:
            assert "AUTOPOPRAWKI" in text or "Źródło:" in text or not text.strip()
        else:
            assert text == source[segment.source_start : segment.source_start + len(text)]


def test_trimmed_body_marker_stays_generated_through_amendment_assembly_and_budget() -> None:
    body = "USTAWA\nArt. 1. " + "x" * 800 + "\fRaport z konsultacji\nREMOVED"
    proposal = DocumentPart(
        role="self_amendment", url="https://source.test/1", text="AUTOPOPRAWKA\n30 -> 60"
    )
    selected = DocumentSelection(parts=(DocumentPart(role="bill", text=body), proposal))
    trimmed = trim_print(body)
    assert trimmed.projection is not None, "trim_print supplies the exact main text map"
    prepared = append_self_amendments(trimmed.text, (proposal,))
    projected = amended_input_projection(selected, trimmed.projection, prepared)
    assert projected is not None, "the main text matches the selected body"
    budget = TextBudget(900).apply(prepared)
    assert budget.projection is not None, "TextBudget supplies its exact map"

    final = budget.projection.compose(projected)

    assert "REMOVED" not in final.text
    assert "[pominięto:" in final.text
    assert all(
        segment.source_start is None
        or segment.source_start + segment.end - segment.start <= body.index("Raport")
        for segment in final.segments
    )


@pytest.mark.parametrize("wrong", ["main", "rendering", "missing"])
def test_unconfirmed_main_or_changed_rendering_remains_unknown(wrong: str) -> None:
    part = DocumentPart(role="self_amendment", text="Proposal")
    selected = DocumentSelection(parts=(DocumentPart(role="bill", text="Body"), part))
    projection = project_pieces("Foreign" if wrong == "main" else "Body", (SourcePiece("Body"),))
    prepared = append_self_amendments("Body", (part,))
    if wrong == "rendering":
        prepared += "ADDED"
    if wrong == "missing":
        selected = DocumentSelection(parts=(part,))

    assert amended_input_projection(selected, projection, prepared) is None
