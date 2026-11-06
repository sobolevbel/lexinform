import hashlib

import pytest

from lexinform.adapters.document_text import FlatOpcFootnoteLink, WordXmlTextExtractor

_PACKAGE = "http://schemas.microsoft.com/office/2006/xmlPackage"
_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CALL = '<w:footnoteReference w:id="1"/>'
_NOTE = '<w:footnote w:id="1"><w:p><w:r><w:t>100 zł</w:t></w:r></w:p></w:footnote>'


def _part(name: str, xml: str) -> str:
    return f'<pkg:part pkg:name="/word/{name}.xml"><pkg:xmlData>{xml}</pkg:xmlData></pkg:part>'


def _package(*, calls: str = _CALL, definitions: str = _NOTE, extra: str = "") -> bytes:
    body = f"<w:document><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Art. 1</w:t>{calls}</w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>"
    return (
        f'<pkg:package xmlns:pkg="{_PACKAGE}" xmlns:w="{_WORD}">'
        f"{_part('document', body)}{_part('footnotes', f'<w:footnotes>{definitions}</w:footnotes>')}"
        f"{extra}</pkg:package>"
    ).encode()


def test_flat_opc_proof_binds_legacy_table_call_and_original_package() -> None:
    data = _package()
    extractor = WordXmlTextExtractor()

    proof = extractor.observe_flat_opc_notes(data)

    assert proof is not None, "the package contains a unique supported note used in a table cell"
    assert proof.input_sha256 == hashlib.sha256(data).hexdigest()
    text = extractor.extract(data)
    assert text == "Art. 1 [przypis 1: 100 zł] "
    assert proof.legacy_text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert proof.footnote_links == (
        FlatOpcFootnoteLink(
            identity="1",
            call_path=(0, 0, 0, 0, 0, 0, 0, 0, 0, 1),
            paragraph_path=(0, 0, 0, 0, 0, 0, 0, 0),
            definition_path=(1, 0, 0, 0),
        ),
    )
    assert proof.unproven_footnote_calls == 0
    changed = extractor.observe_flat_opc_notes(data.replace(b"100", b"200"))
    assert changed is not None, "the amount mutation preserves the note structure"
    assert changed.footnote_links == proof.footnote_links
    assert changed.input_sha256 != proof.input_sha256
    assert changed.legacy_text_sha256 != proof.legacy_text_sha256


def test_flat_opc_only_links_calls_with_exact_supported_definitions() -> None:
    data = _package(calls=_CALL + '<w:footnoteReference w:id="2"/>')
    extractor = WordXmlTextExtractor()

    proof = extractor.observe_flat_opc_notes(data)

    assert proof is not None, "one of the two visited footnote calls has an exact origin"
    assert [link.identity for link in proof.footnote_links] == ["1"]
    assert proof.unproven_footnote_calls == 1
    assert "[nieodczytany przypis 2]" in extractor.extract(data)


@pytest.mark.parametrize(
    "data",
    [
        _package(definitions=""),
        _package(definitions=_NOTE * 2),
        _package(definitions=_NOTE.replace("<w:footnote ", '<w:footnote w:type="separator" ')),
        _package(extra=_part("footnotes", f"<w:footnotes>{_NOTE}</w:footnotes>")),
        _package(extra=f"<wrapper>{_part('endnotes', '<w:endnotes/>')}</wrapper>"),
        _package(extra=_part("endnotes", f"<w:endnotes>{_NOTE}</w:endnotes>")),
        _package(calls=_CALL * 65),
        b" " * 4096 + b'<!DOCTYPE x [<!ENTITY x "unsafe">]>' + _package(),
        _package().decode().encode("utf-16"),
        b'<?xml version="1.0" encoding="unknown"?>' + _package(),
        b'<?xml version="1.0" encoding="shift_jis"?>' + _package(),
        f'<w:document xmlns:w="{_WORD}"><w:body><w:p/></w:body></w:document>'.encode(),
    ],
)
def test_flat_opc_proof_refuses_unverifiable_packages_and_overflow(data: bytes) -> None:
    assert WordXmlTextExtractor().observe_flat_opc_notes(data) is None
