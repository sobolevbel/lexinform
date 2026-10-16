import io
import json
import zipfile
from pathlib import Path

import pytest

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_print
from lexinform.models import RclProject

FIXTURES = Path(__file__).parents[1] / "fixtures/document_selection"


def project(identity: int) -> RclProject:
    return RclProject.model_validate_json((FIXTURES / f"rcl_{identity}.json").read_bytes())


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="B97: an older RCL version wins by file order"
)
def test_new_rcl_text_and_its_justification_supersede_the_previous_version() -> None:
    picked = project(12410906).text_documents()

    assert picked["bill"].id == 791866
    assert picked["justification"].id == 791867
    assert picked["osr"].id == 791873


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="B99: a draft regulation hides the available bill"
)
def test_rcl_implementing_regulation_does_not_replace_the_bill() -> None:
    picked = project(12321775).text_documents()

    assert picked["bill"].id == 418603


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="B100: za zgodność is mistaken for a compliance table",
)
def test_certified_bill_is_not_a_compliance_table() -> None:
    picked = project(12400754).text_documents()

    assert "bill" in picked
    assert picked["bill"].id == 730985


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="B101: a staffing annex is picked as the budget bill"
)
def test_budget_print_selects_its_act_instead_of_the_first_pdf() -> None:
    info = parse_print(json.loads((FIXTURES / "print_8_2864.json").read_bytes()), term=8)

    assert info.main_pdf is not None
    assert info.main_pdf.name == "2864  - ustawa i załączniki.pdf"


def word(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


def archive_of(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="B98: the first nested ZIP has only a justification"
)
def test_a_nested_regulation_justification_cannot_hide_the_sibling_bill_package() -> None:
    wrong = archive_of(
        [("UZASADNIENIE.docx", word("UZASADNIENIE\nNowelizacja rozporządzenia ministra."))]
    )
    law = "USTAWA\nz dnia 7 kwietnia 2025 r.\nArt. 1. Termin wynosi 30 dni."
    right = archive_of([("Projekt ustawy.docx", word(law))])
    package = archive_of([("PROJ.zip", wrong), ("PROJ_UST_zm.zip", right)])
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())

    text = extractor.extract(package)

    assert law in text
    assert "Nowelizacja rozporządzenia" not in text


def test_compliance_table_names_remain_excluded() -> None:
    original = project(12400754)
    stage = original.stages[0]
    folder = next(folder for folder in stage.folders if folder.kind == "project")
    bill = next(doc for doc in folder.documents if doc.id == 730985)
    table = bill.model_copy(update={"name": "Tabela zgodności projektu ustawy.pdf"})
    changed = original.with_stage(
        stage.model_copy(update={"folders": (folder.model_copy(update={"documents": (table,)}),)})
    )

    assert "bill" not in changed.text_documents()
