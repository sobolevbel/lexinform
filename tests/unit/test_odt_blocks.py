import hashlib
import io
import zipfile
from collections.abc import Mapping
from xml.etree import ElementTree as ET

import pytest

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import (
    DocumentTextExtractor,
    DocxTextExtractor,
    OdtTextExtractor,
)
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import (
    ExtractedParts,
    OdtIgnoredNode,
    OdtPartExtraction,
    OdtTextBlock,
    OdtTextExtraction,
)
from tests.fakes import FakeTextExtractor

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"


def package(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def odt_document(body: str, *, missing_body: bool = False, doctype: bool = False) -> bytes:
    document = f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}" xmlns:table="{TABLE}">'
    document += (
        body
        if missing_body
        else "<office:body><office:text>" + body + "</office:text></office:body>"
    )
    document += "</office:document-content>"
    if doctype:
        document = "<!DOCTYPE content []>" + document
    return package(
        {"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": document.encode()}
    )


def router(*, odt: FakeTextExtractor | None = None) -> DocumentTextExtractor:
    return DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor(), odt)


def test_odt_origins_preserve_empty_edges_sections_lists_and_nested_table_projection() -> None:
    raw = odt_document(
        "<text:p/><text:h>Tytuł</text:h><text:section><text:list><text:list-item>"
        "<text:p>Lista</text:p></text:list-item></text:list></text:section>"
        "<table:table><table:table-header-rows><table:table-row><table:table-cell>"
        "<text:p>A</text:p><text:p>B</text:p><table:table><table:table-row>"
        "<table:table-cell><text:p>NESTED</text:p></table:table-cell></table:table-row>"
        "</table:table></table:table-cell><table:covered-table-cell/>"
        "<table:table-cell><text:p>C</text:p></table:table-cell></table:table-row>"
        "</table:table-header-rows></table:table><text:p/>"
    )
    extractor = OdtTextExtractor()

    result = extractor.extract_odt_blocks(raw)

    expected = "\nTytuł\nLista\nA B NESTED\t\tC\n"
    assert result.legacy_text == extractor.extract(raw) == expected
    assert result.legacy_text.count("NESTED") == 1
    assert result.input_sha256 == hashlib.sha256(raw).hexdigest()
    assert [block.path for block in result.blocks] == sorted(block.path for block in result.blocks)
    kinds = {block.kind for block in result.blocks}
    assert kinds == {"paragraph", "heading", "table_row", "table_cell"}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        xml = archive.read("content.xml")
    body = ET.fromstring(xml).find(f"{{{OFFICE}}}body/{{{OFFICE}}}text")
    assert body is not None, "odt_document puts the fixture under office:text"
    assert result.xml_sha256 == hashlib.sha256(xml).hexdigest()
    for block in result.blocks:
        node = body
        for index in block.path:
            node = node[index]
        assert node.tag.rsplit("}", 1)[-1] in {
            "p",
            "h",
            "table-row",
            "table-cell",
            "covered-table-cell",
        }
        assert block.text_sha256 == hashlib.sha256(block.text.encode()).hexdigest()
    assert OdtTextExtraction.model_validate_json(result.model_dump_json()) == result
    assert extractor.extract_odt_blocks(raw).manifest_sha256 == result.manifest_sha256


def test_unhandled_body_nodes_and_unresolved_inline_views_remain_explicit() -> None:
    raw = odt_document(
        "<text:tracked-changes><text:changed-region/></text:tracked-changes>"
        "<text:p>Art. 1.<text:note><text:note-citation>1</text:note-citation>"
        "<text:note-body><text:p>Przypis</text:p></text:note-body></text:note></text:p>"
        "<table:table><table:table-column/><table:table-rows><table:table-row>"
        "<text:unknown/><table:table-cell><text:p>Kwota</text:p></table:table-cell>"
        "</table:table-row></table:table-rows></table:table>"
    )

    result = OdtTextExtractor().extract_odt_blocks(raw)

    assert result.legacy_text == "Art. 1.1Przypis\nKwota"
    assert [node.path for node in result.ignored_nodes] == [(0,), (2, 0), (2, 1, 0, 0)]
    assert result.inline_notes == "legacy-inline-unmapped"
    assert result.tracked_changes == "legacy-inline-unresolved"
    assert result.table_repetitions == "legacy-unexpanded"


@pytest.mark.parametrize("missing", [False, True])
def test_missing_and_refused_xml_do_not_claim_blocks(missing: bool) -> None:
    raw = odt_document("", missing_body=missing, doctype=not missing)
    extractor = OdtTextExtractor()

    result = extractor.extract_odt_blocks(raw)

    assert result.body_state == ("missing" if missing else "unsupported")
    assert result.legacy_text == extractor.extract(raw) == ""
    assert not result.blocks and not result.legacy_blocks and not result.ignored_nodes


def test_nested_packages_bind_odt_origins_to_the_exact_physical_member() -> None:
    raw = odt_document("<text:p>USTAWA o cudzoziemcach</text:p>")
    inner = package({"projekt.odt": raw})
    outer = package({"materiały.zip": inner})
    extractor = router()

    physical = extractor.extract_parts(outer)
    disabled = extractor.extract_parts(outer, include_odt_blocks=False)

    assert physical.parts == disabled.parts and physical.rejected == disabled.rejected
    assert len(physical.odt_blocks) == 1 and not disabled.odt_blocks
    assert physical.parts[0].members == ("materiały.zip", "projekt.odt")
    assert physical.odt_blocks[0].part_index == 0
    assert physical.odt_blocks[0].extraction.input_sha256 == physical.parts[0].sha256
    assert physical.parts[0].sha256 == hashlib.sha256(raw).hexdigest()
    assert extractor.extract_document(raw).text == physical.parts[0].text


def test_a_custom_odt_adapter_without_coordinates_keeps_its_legacy_result() -> None:
    raw = odt_document("<text:p>Oryginał</text:p>")

    extracted = router(odt=FakeTextExtractor("Adapter text")).extract_parts(raw)

    assert extracted.parts[0].text == "Adapter text" and not extracted.odt_blocks


@pytest.mark.parametrize("damage", ["outside", "duplicate", "hash", "text"])
def test_part_manifests_reject_false_physical_bindings(damage: str) -> None:
    extracted = router().extract_parts(odt_document("<text:p>Art. 1.</text:p>"))
    original = extracted.odt_blocks[0]
    changed: tuple[OdtPartExtraction, ...]
    if damage == "outside":
        changed = (original.model_copy(update={"part_index": 1}),)
    elif damage == "duplicate":
        changed = (original, original)
    elif damage == "hash":
        changed = (
            original.model_copy(
                update={
                    "extraction": original.extraction.model_copy(update={"input_sha256": "0" * 64})
                }
            ),
        )
    else:
        blocks = (original.extraction.blocks[0].model_copy(update={"text": "Changed"}),)
        changed = (
            original.model_copy(
                update={"extraction": original.extraction.model_copy(update={"blocks": blocks})}
            ),
        )

    with pytest.raises(ValueError):
        ExtractedParts(
            parts=extracted.parts, input_sha256=extracted.input_sha256, odt_blocks=changed
        )


def test_rejected_xml_and_archive_limits_leave_no_dangling_manifests() -> None:
    malformed = odt_document("<text:p>Broken")
    extractor = router()

    broken = extractor.extract_parts(malformed)
    limited = DocumentTextExtractor(
        PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor(), max_member_bytes=1
    ).extract_parts(package({"projekt.odt": odt_document("<text:p>Too large</text:p>")}))

    assert not broken.parts and not broken.odt_blocks and broken.rejected
    assert not limited.parts and not limited.odt_blocks and limited.rejected
    with pytest.raises(ET.ParseError):
        OdtTextExtractor().extract_odt_blocks(malformed)


@pytest.mark.parametrize("damage", ["order", "duplicate", "projection", "ignored", "unread"])
def test_odt_model_rejects_unverifiable_coordinates_and_projection(damage: str) -> None:
    result = OdtTextExtractor().extract_odt_blocks(
        odt_document("<text:p>A</text:p><text:p>B</text:p>")
    )
    value = result.model_dump()
    if damage == "order":
        value["blocks"] = tuple(reversed(result.blocks))
    elif damage == "duplicate":
        value["blocks"] = (result.blocks[0], result.blocks[0])
    elif damage == "projection":
        value["legacy_blocks"] = ("odt-xml:content.xml:body:99",)
    elif damage == "ignored":
        value["ignored_nodes"] = (OdtIgnoredNode(path=result.blocks[0].path, tag="same"),)
    else:
        value["body_state"] = "missing"

    with pytest.raises(ValueError):
        OdtTextExtraction.model_validate(value)


@pytest.mark.parametrize(
    "text",
    [
        "Kwota 300 zł.",
        "Termin 2027-01-01.",
        "Nie stosuje się.",
        "Wyłącznie cudzoziemcy.",
        "Art. 21 ust. 3.",
    ],
)
def test_meaningful_text_is_preserved_in_the_physical_block_and_projection(text: str) -> None:
    raw = odt_document("<text:p>" + text + "</text:p>")

    result = OdtTextExtractor().extract_odt_blocks(raw)

    assert result.legacy_text == text
    assert result.blocks == (OdtTextBlock(kind="paragraph", path=(0,), text=text),)
