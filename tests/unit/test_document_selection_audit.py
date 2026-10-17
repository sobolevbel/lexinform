import io
import json
import zipfile
from pathlib import Path

import pytest

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_print
from lexinform.law_digest import law_digest
from lexinform.models import RclProject
from lexinform.sections import PAGE_BREAK, trim_print

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


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="B102: the declared ZIP bill uses Deflate64"
)
def test_deflate64_package_does_not_silently_discard_its_bill() -> None:
    data = (FIXTURES / "deflate64_771111.zip").read_bytes()
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())

    text = extractor.extract(data)

    assert "Projekt z dnia 2 kwietnia 2026 r." in text
    assert "inwestycji uzupełniającej" in text


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="B103: a real RCL package uses three ZIP levels"
)
def test_signed_nested_bill_package_is_not_treated_as_an_empty_document() -> None:
    law = "USTAWA\nz dnia 17 marca 2025 r.\nArt. 1. Termin wynosi 30 dni."
    inner = archive_of([("1. Projekt ustawy/Projekt ustawy.docx", word(law))])
    signed = archive_of(
        [("1. Projekt ustawy.zip", inner), ("1. Projekt ustawy.zip.xades", b"signature")]
    )
    outer = archive_of([("1__Projekt_ustawy.zip", signed)])
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())

    text = extractor.extract(outer)

    assert law in text


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="B105: a normative annex is discarded by its name"
)
def test_statutory_annex_with_the_affected_services_survives_package_selection() -> None:
    law = "USTAWA\nz dnia 20 maja 2026 r.\nArt. 1. Dodaje się załącznik nr 16 w brzmieniu określonym w załączniku nr 2 do niniejszej ustawy."
    annex = "Załącznik nr 2\nWYKAZ USŁUG, O KTÓRYCH MOWA W ART. 105A UST. 1 PKT 2 USTAWY\n62.02.2\tUsługi związane z doradztwem w zakresie informatyki."
    package = archive_of(
        [
            ("Projekt ustawy.docx", word(law)),
            ("2026-05-20 zalacznik nr 2 do ustawy na RM.docx", word(annex)),
        ]
    )
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())

    text = extractor.extract(package)

    assert law in text
    assert "62.02.2" in text


def test_deeper_support_must_not_replace_a_bill_with_a_regulation_justification() -> None:
    law = "USTAWA\nz dnia 18 marca 2025 r.\nArt. 1. Termin wynosi 30 dni."
    justification = "UZASADNIENIE\nPotrzeba opracowania projektu rozporządzenia Ministra."
    deep = archive_of(
        [("Projekt rozporządzenia.zip", archive_of([("Uzasadnienie.docx", word(justification))]))]
    )
    good = archive_of([("Projekt ustawy.docx", word(law))])
    package = archive_of([("Zal. 2-9.zip", deep), ("Zal.zip", good)])
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())

    text = extractor.extract(package)

    assert law in text
    assert justification not in text


def investment_package(*, annex_before_reasons: bool) -> str:
    law = (
        "USTAWA\nz dnia 7 kwietnia 2025 r.\n"
        "Art. 1. Wykaz strategicznych inwestycji w zakresie sieci przesyłowych "
        "określa załącznik do niniejszej ustawy.\n"
        "Art. 2. Inwestor realizuje inwestycje określone w wykazie na zasadach "
        "określonych w ustawie.\nArt. 3. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia."
    )
    annex = "Załącznik do ustawy\nWYKAZ INWESTYCJI\n1. Budowa linii 400 kV."
    reasons = "UZASADNIENIE\nProjekt określa wykaz strategicznych inwestycji."
    osr = (
        "Nazwa projektu\nProjekt ustawy o inwestycjach\nOCENA SKUTKÓW REGULACJI\n"
        "4. Podmioty, na które oddziałuje projekt\nInwestorzy\n"
        "5. Informacje na temat zakresu, czasu trwania i podsumowanie wyników konsultacji\n"
        "Projekt skierowano do konsultacji publicznych."
    )
    parts = (law, annex, reasons, osr) if annex_before_reasons else (law, reasons, osr, annex)
    return PAGE_BREAK.join(parts)


def extracted_investment_package(voltage: int) -> str:
    law, reasons, osr, annex = investment_package(annex_before_reasons=False).split(PAGE_BREAK)
    package = archive_of(
        [
            ("Projekt ustawy.docx", word(law)),
            ("Uzasadnienie.docx", word(reasons)),
            ("OSR.docx", word(osr)),
            ("Załącznik do ustawy.docx", word(annex.replace("400 kV", f"{voltage} kV"))),
        ]
    )
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    return extractor.extract(package)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="B105: the selected package must retain its normative annex through trimming",
)
def test_selected_normative_annex_survives_extraction_and_trimming() -> None:
    text = extracted_investment_package(400)

    trimmed = trim_print(text).text

    assert "Budowa linii 400 kV" in trimmed


@pytest.mark.parametrize(
    "annex_before_reasons",
    [
        True,
        pytest.param(
            False,
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="B105: a substantive annex mutation is invisible after OSR",
            ),
        ),
    ],
)
def test_normative_annex_voltage_change_changes_the_law_digest(annex_before_reasons: bool) -> None:
    if annex_before_reasons:
        original = investment_package(annex_before_reasons=True)
        changed = original.replace("400 kV", "500 kV")
    else:
        original = extracted_investment_package(400)
        changed = extracted_investment_package(500)

    previous = law_digest(trim_print(original).text)
    current = law_digest(trim_print(changed).text)

    assert previous is not None and current is not None
    assert current != previous
