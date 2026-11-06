"""Bind optional Word formula source data to an already fitted ordinary question."""

from lexinform.models.analysis import BillContext
from lexinform.models.document_history import SourceObservationIdentity
from lexinform.models.input_receipts import PrimaryInputReceipt
from lexinform.models.word_formula_input import (
    WordFormulaInputEntry,
    WordFormulaInputV1,
    encode_formula_tree,
)
from lexinform.services.document_history import DocumentSourceRead
from lexinform.text_projection import TextProjection, TextSegment
from lexinform.word_formula_placement import WordFormulaSource, retained_formula_placements


def word_formula_input(
    word: WordFormulaSource,
    source: DocumentSourceRead,
    receipt: PrimaryInputReceipt,
    ctx: BillContext,
    *,
    term: int,
) -> WordFormulaInputV1 | None:
    selection = source.selection
    document = source.document
    artifact = receipt.artifact_sha256
    if (
        document.kind != "rcl"
        or document.official_link_conflict
        or document.required_urls
        or document.normative_urls
        or document.amendment_urls
        or ctx.truncated
        or ctx.scan is not None
        or ctx.source_kind != "rcl"
        or ctx.text_source != "documents"
        or receipt.retention != "complete"
        or receipt.source_part.role != "bill"
        or receipt.extraction_id is None
        or selection.selection_id != receipt.source_selection_id
        or selection.selection.state != "selected"
        or len(selection.selection.parts) != 1
        or artifact is None
        or artifact != word.body.input_sha256
        or not any(item.sha256 == artifact for item in source.artifacts)
        or not any(
            item.sha256 == artifact and item.requested_url == document.url for item in source.reads
        )
        or not any(
            item.result_id == receipt.extraction_id and item.artifact_sha256 == artifact
            for item in source.extractions
        )
    ):
        return None
    projection = TextProjection(
        ctx.text,
        receipt.source_part.text_sha256,
        receipt.source_part.chars,
        tuple(TextSegment(part.start, part.end, part.source_start) for part in receipt.segments),
    )
    placements = retained_formula_placements(word.body, word.scan, projection)
    if not placements:
        return None
    formulas = []
    for placement, formula in zip(placements, word.scan.formulas, strict=True):
        if formula.structure is None:
            return None
        formulas.append(
            WordFormulaInputEntry(
                paragraph_path=placement.paragraph_path,
                formula_path=placement.formula_path,
                subtree_sha256=placement.formula_sha256,
                source_anchor=placement.source_span,
                emitted_anchor=placement.emitted_span,
                source_gap=placement.source_insert_at,
                emitted_gap=placement.emitted_insert_at,
                tree=encode_formula_tree(formula.structure),
            )
        )
    return WordFormulaInputV1(
        source_observation_id=SourceObservationIdentity(
            term=term,
            number=ctx.number,
            purpose="analysis",
            document=document,
            selection_id=selection.selection_id,
        ).observation_id,
        selection_id=selection.selection_id,
        artifact_sha256=artifact,
        document_xml_sha256=word.scan.document_xml_sha256,
        formulas=tuple(formulas),
    )
