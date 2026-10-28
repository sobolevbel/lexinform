import hashlib
from html import escape
from typing import Never

import pytest

from lexinform.adapters import document_text
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import ExtractedParts, UnlocatedPartExtraction, UnlocatedTextExtraction
from tests.fakes import FakeTextExtractor
from tests.unit.test_odt_blocks import odt_document, package
from tests.unit.test_word_blocks import paragraph, word

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def word_xml(text: str, *, flat: bool = False) -> bytes:
    namespace = (
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        if flat
        else "http://schemas.microsoft.com/office/word/2003/wordml"
    )
    root = "document" if flat else "wordDocument"
    document = (
        f'<w:{root} xmlns:w="{namespace}"><w:body><w:p><w:r><w:t>'
        f"{escape(text)}</w:t></w:r></w:p></w:body></w:{root}>"
    )
    if flat:
        document = (
            '<pkg:package xmlns:pkg="http://schemas.microsoft.com/office/2006/xmlPackage">'
            '<pkg:part pkg:name="/word/document.xml"><pkg:xmlData>'
            + document
            + "</pkg:xmlData></pkg:part></pkg:package>"
        )
    return ('<?xml version="1.0" encoding="UTF-8"?>' + document).encode()


def router(*, custom: bool = False) -> DocumentTextExtractor:
    return DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        FakeTextExtractor() if custom else DocTextExtractor(),
    )


@pytest.mark.parametrize("flat", [False, True])
def test_word_xml_projection_records_unknown_coordinates_without_inferred_pages(flat: bool) -> None:
    raw = word_xml("Art. 1. Kwota 100 zł.", flat=flat)
    reader = router()

    result = reader.extract_parts(raw)
    disabled = reader.extract_parts(raw, include_unlocated_text=False)

    assert result.parts == disabled.parts and result.rejected == disabled.rejected
    assert not disabled.unlocated_text
    assert len(result.unlocated_text) == 1 and result.unlocated_text[0].part_index == 0
    metadata = result.unlocated_text[0].extraction
    assert metadata.route == "word_xml" and metadata.coordinates == "unknown"
    assert metadata.pagination == "unknown" and metadata.note_origins == "unlocated"
    assert metadata.tracked_changes == metadata.field_origins == "unknown"
    assert metadata.text_chars == len(result.parts[0].text)
    assert metadata.text_sha256 == hashlib.sha256(result.parts[0].text.encode()).hexdigest()
    assert metadata.input_sha256 == hashlib.sha256(raw).hexdigest()
    assert UnlocatedTextExtraction.model_validate_json(metadata.model_dump_json()) == metadata
    assert ExtractedParts.model_validate_json(result.model_dump_json()) == result
    assert reader.extract_document(raw).text == result.parts[0].text == "Art. 1. Kwota 100 zł."


@pytest.mark.parametrize("custom", [False, True])
def test_doc_route_keeps_unlocated_status_for_empty_or_custom_projection(custom: bool) -> None:
    raw = OLE + b"controlled synthetic fixture"

    result = router(custom=custom).extract_parts(raw)

    assert len(result.unlocated_text) == 1
    metadata = result.unlocated_text[0].extraction
    assert metadata.route == "doc" and metadata.coordinates == "unknown"
    assert metadata.text_chars == len(result.parts[0].text)
    assert metadata.text_sha256 == hashlib.sha256(result.parts[0].text.encode()).hexdigest()
    if not custom:
        assert not result.parts[0].text and result.parts[0].losses == ("no extracted text",)


def test_nested_members_keep_distinct_unknown_origin_links_even_for_same_bytes() -> None:
    raw = word_xml("Termin 1 stycznia 2027 r.")
    outer = package({"one.xml": raw, "nested.zip": package({"renamed.pdf": raw})})

    result = router().extract_parts(outer)

    assert len(result.parts) == len(result.unlocated_text) == 2
    assert [result.parts[item.part_index].members for item in result.unlocated_text] == [
        ("nested.zip", "renamed.pdf"),
        ("one.xml",),
    ]
    assert len({item.extraction.manifest_sha256 for item in result.unlocated_text}) == 1
    assert all(part.container_sha256 == hashlib.sha256(outer).hexdigest() for part in result.parts)


@pytest.mark.parametrize("fault", ["part", "bytes", "text", "chars", "duplicate"])
def test_unknown_origin_manifest_cannot_claim_a_different_part(fault: str) -> None:
    extracted = router().extract_parts(word_xml("Art. 1. Przepis."))
    link = extracted.unlocated_text[0]
    changed: tuple[UnlocatedPartExtraction, ...]
    if fault == "part":
        changed = (link.model_copy(update={"part_index": 1}),)
    elif fault == "duplicate":
        changed = (link, link)
    else:
        field, value = {
            "bytes": ("input_sha256", "0" * 64),
            "text": ("text_sha256", "0" * 64),
            "chars": ("text_chars", 0),
        }[fault]
        changed = (
            link.model_copy(
                update={"extraction": link.extraction.model_copy(update={field: value})}
            ),
        )

    with pytest.raises(ValueError):
        ExtractedParts(
            parts=extracted.parts, input_sha256=extracted.input_sha256, unlocated_text=changed
        )


@pytest.mark.parametrize("kind", ["word", "odt"])
def test_located_part_cannot_also_claim_unknown_coordinates(kind: str) -> None:
    raw = word(paragraph("A")) if kind == "word" else odt_document("<text:p>A</text:p>")
    extracted = router().extract_parts(raw)
    part = extracted.parts[0]
    link = UnlocatedPartExtraction(
        part_index=0,
        extraction=UnlocatedTextExtraction(
            route="word_xml",
            input_sha256=part.sha256,
            text_sha256=part.text_sha256,
            text_chars=len(part.text),
        ),
    )

    with pytest.raises(ValueError, match="without coordinates"):
        ExtractedParts.model_validate({**extracted.model_dump(), "unlocated_text": (link,)})


@pytest.mark.parametrize(
    "text",
    [
        "Termin: 1 stycznia 2027 r.",
        "Kwota 300 zł.",
        "Nie stosuje się.",
        "Wyłącznie cudzoziemcy.",
        "Art. 21 ust. 3.",
    ],
)
def test_meaningful_word_xml_changes_remain_in_the_bound_text_identity(text: str) -> None:
    reader = router()
    before = reader.extract_parts(word_xml("Art. 1. Przepis."))
    after = reader.extract_parts(word_xml("Art. 1. Przepis. " + text))

    assert text in after.parts[0].text
    assert (
        before.unlocated_text[0].extraction.text_sha256
        != after.unlocated_text[0].extraction.text_sha256
    )
    assert (
        before.unlocated_text[0].extraction.manifest_sha256
        != after.unlocated_text[0].extraction.manifest_sha256
    )


def test_legacy_document_facade_does_not_construct_unlocated_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = word_xml("Art. 1. Tekst ustawy.")

    def refused(**kwargs: object) -> Never:
        raise AssertionError("Unlocated metadata was requested")

    monkeypatch.setattr(document_text, "UnlocatedTextExtraction", refused)

    assert router().extract_document(raw).text == "Art. 1. Tekst ustawy."
    with pytest.raises(AssertionError, match="metadata was requested"):
        router().extract_parts(raw)
