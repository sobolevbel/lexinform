import hashlib
import io
import zipfile
from typing import Never

import pytest

from lexinform.adapters import document_text
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from tests.fakes import FakeTextExtractor


def archive(members: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        for name, data in members.items():
            output.writestr(name, data)
    return stream.getvalue()


def docx(text: str) -> bytes:
    return archive(
        {
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                f"{text}</w:t></w:r></w:p></w:body></w:document>"
            ).encode()
        }
    )


def extractor(*, max_member_bytes: int | None = None) -> DocumentTextExtractor:
    return DocumentTextExtractor(
        FakeTextExtractor("PDF text", page_count=1),
        DocxTextExtractor(),
        FakeTextExtractor("DOC text"),
        max_member_bytes=max_member_bytes,
    )


def refuse_legal_selection(*args: object, **kwargs: object) -> Never:
    raise AssertionError("physical extraction must not decide the legal role or bundle")


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.7 fake bytes",
        docx("USTAWA"),
        archive({"projekt.zip": archive({"projekt.docx": docx("USTAWA")})}),
        b"unknown format",
    ],
)
def test_physical_extraction_never_invokes_legal_selection(
    monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    reader = extractor()
    monkeypatch.setattr(document_text, "document_kind", refuse_legal_selection)
    monkeypatch.setattr(reader, "extract_document", refuse_legal_selection)

    result = reader.extract_parts(data)

    assert result.input_sha256 == hashlib.sha256(data).hexdigest()
    assert len(result.parts) == 1
    assert result.parts[0].role == "unknown"


def test_physical_parts_include_the_files_that_the_legacy_policy_rejects() -> None:
    files = {
        "projekt ustawy.docx": docx("USTAWA"),
        "pismo.docx": docx("Szanowny Panie Marszałku"),
        "tabela zgodnosci.docx": docx("TYTUŁ PROJEKTU"),
        "uzasadnienie.docx": docx("UZASADNIENIE"),
    }
    data = archive({"projekt.zip": archive(files)})
    reader = extractor()

    physical = reader.extract_parts(data)
    selected = reader.extract_document(data)

    assert [part.members for part in physical.parts] == [
        ("projekt.zip", name) for name in sorted(files)
    ]
    assert {part.role for part in physical.parts} == {"unknown"}
    assert not physical.rejected
    assert selected.text == "USTAWA\fUZASADNIENIE"
    assert {part.role for part in selected.selection.parts} == {"bill", "justification"}
    assert len(selected.selection.observed_parts) == len(physical.parts)
    assert selected.selection.rejected
    for part in physical.parts:
        assert part.sha256 == hashlib.sha256(files[part.members[-1]]).hexdigest()
        assert part.container_sha256 == physical.input_sha256
        assert part.text_sha256 == hashlib.sha256(part.text.encode()).hexdigest()
        assert part.text_end == len(part.text)
        assert part.pages == 0 and part.page_start is None and part.page_end is None


def test_physical_unpacking_limits_stay_separate_from_legal_rejections() -> None:
    data = archive({"projekt.docx": docx("USTAWA")})
    reader = extractor(max_member_bytes=10)

    physical = reader.extract_parts(data)
    selected = reader.extract_document(data)

    assert not physical.parts
    assert physical.rejected == ("('projekt.docx',): unpacking limit",)
    assert selected.selection.rejected == physical.rejected
    assert selected.selection.state == "unsupported"


def test_an_unreadable_member_retains_its_identity_and_physical_loss() -> None:
    data = archive({"projekt.pdf": b"not a PDF"})
    reader = extractor()

    physical = reader.extract_parts(data)
    selected = reader.extract_document(data)

    assert len(physical.parts) == 1
    assert physical.parts[0].losses == ("no extracted text",)
    assert physical.parts[0].sha256 == hashlib.sha256(b"not a PDF").hexdigest()
    assert not physical.rejected
    assert selected.selection.state == "unsupported"
    assert selected.selection.rejected == ("('projekt.pdf',): unknown",)


def test_a_corrupt_archive_has_no_invented_members_or_legal_verdict() -> None:
    data = b"PK\x03\x04invalid archive"

    result = extractor().extract_parts(data)

    assert result.input_sha256 == hashlib.sha256(data).hexdigest()
    assert not result.parts
    assert result.rejected == ("(): BadZipFile",)
