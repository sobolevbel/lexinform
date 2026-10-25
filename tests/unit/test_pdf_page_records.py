import hashlib
import io
from collections.abc import Iterator

import pytest
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from lexinform.adapters import pdf_text
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import PdfPageText, PdfTextExtraction
from tests.conftest import FIXTURES


class Page:
    def __init__(self, value: str | None | Exception) -> None:
        self.value = value

    def extract_text(self) -> str | None:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Reader:
    def __init__(self, pages: tuple[Page, ...]) -> None:
        self.pages = pages


def install_pages(
    monkeypatch: pytest.MonkeyPatch, values: tuple[str | None | Exception, ...]
) -> None:
    def read(stream: io.BytesIO) -> Reader:
        return Reader(tuple(Page(value) for value in values))

    monkeypatch.setattr(pdf_text, "PdfReader", read)


def test_raw_page_coordinates_survive_blank_edges_and_legacy_whitespace_normalisation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = ("", "  A \r\n\xa0\n\n\nB  ", "", "C\t \n", "")
    install_pages(monkeypatch, values)
    reader = PypdfTextExtractor()

    result = reader.extract_pages(b"same bytes")

    assert result.page_count == 5 and result.error_type is None
    assert tuple(page.text for page in result.pages) == values
    assert [page.block_id for page in result.pages] == [
        f"pdf-page:{number}:0" for number in range(1, 6)
    ]
    assert reader.extract(b"same bytes") == "A\n\nB  \f\fC"
    assert result.pages[1].text_sha256 == hashlib.sha256(values[1].encode()).hexdigest()


def test_empty_pages_and_failed_pages_keep_distinct_physical_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_pages(monkeypatch, (None, "", RuntimeError("broken text operator"), "readable"))
    reader = PypdfTextExtractor()

    result = reader.extract_pages(b"same bytes")

    assert result.page_count == 4 and result.error_type is None
    assert [page.error_type for page in result.pages] == [None, None, "RuntimeError", None]
    assert [page.text for page in result.pages] == ["", "", "", "readable"]
    assert result.pages[-1].number == 4
    assert reader.extract(b"same bytes") == "readable"


class BrokenPageTree:
    def __iter__(self) -> Iterator[Page]:
        yield Page("prefix before the damaged page tree")
        raise ValueError("damaged page tree")


class BrokenReader:
    pages = BrokenPageTree()


def test_a_late_page_tree_error_keeps_observations_without_claiming_total_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def read(stream: io.BytesIO) -> BrokenReader:
        return BrokenReader()

    monkeypatch.setattr(pdf_text, "PdfReader", read)
    reader = PypdfTextExtractor()

    result = reader.extract_pages(b"same bytes")

    assert result.page_count is None and result.error_type == "ValueError"
    assert result.pages == (PdfPageText(number=1, text="prefix before the damaged page tree"),)
    assert reader.extract(b"same bytes") == ""


@pytest.mark.parametrize("data", [b"", b"%PDF-1.7 truncated", b"not a PDF"])
def test_unreadable_bytes_have_an_identity_and_unknown_page_count(data: bytes) -> None:
    reader = PypdfTextExtractor()

    result = reader.extract_pages(data)

    assert result.input_sha256 == hashlib.sha256(data).hexdigest()
    assert result.error_type is not None and result.page_count is None
    assert not result.pages and reader.extract(data) == ""


def test_real_pdf_blanks_have_physical_positions_before_the_text_projection() -> None:
    original = PdfReader(io.BytesIO((FIXTURES / "print_3039.pdf").read_bytes()))
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_page(original.pages[0])
    writer.add_blank_page(width=612, height=792)
    writer.add_page(original.pages[1])
    writer.add_blank_page(width=612, height=792)
    data = io.BytesIO()
    writer.write(data)
    reader = PypdfTextExtractor()

    result = reader.extract_pages(data.getvalue())

    assert result.page_count == 5
    assert [page.number for page in result.pages if page.text.strip()] == [2, 4]
    assert all(result.pages[index].text == "" for index in (0, 2, 4))
    text = reader.extract(data.getvalue())
    assert not text.startswith("\f") and not text.endswith("\f")
    assert "\f\f" in text and "Druk nr 3039" in text


def test_manifest_is_stable_after_serialisation_and_identifies_bytes_dependency_and_page_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_pages(monkeypatch, ("Kwota wynosi 200 zł.",))
    reader = PypdfTextExtractor()
    result = reader.extract_pages(b"one version")

    restored = PdfTextExtraction.model_validate_json(result.model_dump_json(indent=2))

    assert restored == result and restored.manifest_sha256 == result.manifest_sha256
    assert restored.pages[0].block_id == result.pages[0].block_id
    changed = (
        reader.extract_pages(b"other bytes"),
        result.model_copy(update={"pypdf_version": "another dependency version"}),
        result.model_copy(update={"pages": (PdfPageText(number=1, text="Kwota wynosi 300 zł."),)}),
    )
    assert all(item.manifest_sha256 != result.manifest_sha256 for item in changed)


@pytest.mark.parametrize("numbers", [(2,), (1, 1), (1, 3), (2, 1)])
def test_a_manifest_rejects_reordered_or_missing_physical_positions(
    numbers: tuple[int, ...],
) -> None:
    with pytest.raises(ValidationError, match="consecutive physical positions"):
        PdfTextExtraction(
            input_sha256="a" * 64,
            pypdf_version="test version",
            pages=tuple(PdfPageText(number=number) for number in numbers),
        )


def test_a_failed_page_cannot_supply_text_as_if_its_read_succeeded() -> None:
    with pytest.raises(ValidationError, match="failed page"):
        PdfPageText(number=1, text="unverified partial text", error_type="RuntimeError")
