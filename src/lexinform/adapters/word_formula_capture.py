"""Capture optional formula placement from already-held native Word bytes."""

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.models.extractions import DocumentExtraction
from lexinform.word_formula_placement import WordFormulaSource
from lexinform.word_formulas import read_word_formulas


def capture_word_formula_source(
    reader: DocxTextExtractor, data: bytes, extracted: DocumentExtraction
) -> WordFormulaSource | None:
    parts = extracted.document.selection.parts
    result = extracted.result
    if (
        len(data) > 2_097_152
        or result.physical_part_count != 1
        or len(parts) != 1
        or len(result.selected_parts) != 1
        or result.selected_parts[0].format != "word"
        or parts[0].members
        or parts[0].sha256 != result.artifact_sha256
        or not 0 < len(parts[0].text) <= 32_768
    ):
        return None
    scan = read_word_formulas(data)
    if (
        scan.coverage != "complete_body_walk"
        or not 0 < len(scan.formulas) <= 4
        or any(formula.structure is None for formula in scan.formulas)
    ):
        return None
    body = reader.extract_blocks(data)
    if (
        len(body.blocks) > 512
        or body.input_sha256 != result.artifact_sha256
        or body.xml_sha256 != scan.document_xml_sha256
        or body.legacy_text != parts[0].text
    ):
        return None
    return WordFormulaSource(body, scan)
