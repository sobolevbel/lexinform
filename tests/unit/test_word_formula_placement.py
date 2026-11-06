import hashlib

from tools.replay_word_formula_placement import retained_formula_placements

from lexinform.models.documents import WordTextBlock, WordTextExtraction
from lexinform.text_projection import SourcePiece, project_pieces
from lexinform.word_formulas import FormulaObservation, MathNode, WordFormulaScan


def test_position_is_bound_to_the_native_copy_even_with_repeated_text() -> None:
    checksum = hashlib.sha256(b"test").hexdigest()
    blocks = tuple(
        WordTextBlock(kind="paragraph", path=(0, index), text=text)
        for index, text in enumerate(("previous", "", "where", "previous", "", "where"))
    )
    body = WordTextExtraction(
        input_sha256=checksum,
        xml_sha256=checksum,
        python_version="test",
        body_state="read",
        blocks=blocks,
        legacy_blocks=tuple(block.block_id for block in blocks),
    )
    scan = WordFormulaScan(
        checksum,
        "complete_body_walk",
        (FormulaObservation((0, 4, 0), (0, 4), checksum, MathNode("oMath"), None),),
    )
    source = body.legacy_text
    retained = project_pieces(source, (SourcePiece(source, 0),))
    placement = retained_formula_placements(body, scan, retained)

    assert placement is not None, "The exact second instance survives"
    (anchor,) = placement
    assert anchor.source_span == anchor.emitted_span == (16, 31)
    assert anchor.source_insert_at == anchor.emitted_insert_at == 25

    earlier = project_pieces(source, (SourcePiece(source[:15], 0),))
    assert retained_formula_placements(body, scan, earlier) is None
    duplicate = project_pieces(source, (SourcePiece(source, 0), SourcePiece(source, 0)))
    assert retained_formula_placements(body, scan, duplicate) is None
    generated = project_pieces(source, (SourcePiece(source),))
    assert retained_formula_placements(body, scan, generated) is None
    wrong_xml = WordFormulaScan("0" * 64, scan.coverage, scan.formulas)
    assert retained_formula_placements(body, wrong_xml, retained) is None
