import datetime as dt
import hashlib
import io
import zipfile

import pytest
from pydantic import ValidationError

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_selection import bill_selection
from lexinform.document_versions import comparable_versions, draft_version
from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.services.documents import TextLoader
from tests.scenario.test_document_selection_contract import law
from tests.unit.test_document_selection_audit import archive_of, word


def extractor() -> DocumentTextExtractor:
    return DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())


def test_nested_parts_keep_their_outer_identity_and_member_chain() -> None:
    source = word(law())
    package = archive_of([("signed.zip", archive_of([("Projekt.docx", source)]))])

    result = extractor().extract_document(package)

    part = result.selection.parts[0]
    assert part.members == ("signed.zip", "Projekt.docx")
    assert part.container_sha256 == hashlib.sha256(package).hexdigest()
    assert part.sha256 == hashlib.sha256(source).hexdigest()
    assert part.text_sha256 == hashlib.sha256(law().encode()).hexdigest()
    assert part.text_end == len(law())


def test_unknown_quality_cannot_be_promoted_to_selected() -> None:
    assert DocumentSelection().state == "unverified"
    legacy = DocumentSelection(state="selected", role=("filename",))
    assert legacy.state == "unverified"


def test_conflicting_archive_versions_are_not_resolved_by_member_order() -> None:
    members = [("Projekt.pdf.docx", word(law())), ("Projekt.docx", word(law(days=60)))]

    first = extractor().extract_document(archive_of(members))
    reversed_result = extractor().extract_document(archive_of(list(reversed(members))))

    assert first.selection.state == reversed_result.selection.state == "ambiguous"
    assert first.text == reversed_result.text == ""


def test_internal_version_and_subject_confirm_a_new_full_replacement() -> None:
    package = archive_of(
        [
            ("old.zip", archive_of([("Projekt.docx", word(law()))])),
            ("new.zip", archive_of([("Projekt po autopoprawce.docx", word(law(day=2, days=60)))])),
        ]
    )

    result = extractor().extract_document(package)

    assert law(day=2, days=60) in result.text
    assert law() not in result.text


def test_a_shallow_unknown_note_cannot_hide_a_confirmed_nested_bill() -> None:
    package = archive_of(
        [
            ("Informacja.docx", word("Informacja ministra. " * 30)),
            ("Projekt.zip", archive_of([("Projekt.docx", word(law()))])),
        ]
    )

    result = extractor().extract_document(package)

    assert law() in result.text and "Informacja ministra" not in result.text


def test_archive_depth_limit_is_an_extraction_limitation() -> None:
    package = archive_of([("Projekt.docx", word(law()))])
    for _ in range(5):
        package = archive_of([("inner.zip", package)])

    result = extractor().extract_document(package)

    assert result.selection.state == "unsupported"
    assert any("depth limit" in reason for reason in result.selection.rejected)


def test_referenced_word_notes_are_read_at_the_reference() -> None:
    buffer = io.BytesIO()
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document {ns}><w:body><w:p><w:r><w:t>Art. 1. Termin</w:t><w:footnoteReference w:id="2"/><w:t> wynosi 30 dni.</w:t></w:r></w:p></w:body></w:document>',
        )
        archive.writestr(
            "word/footnotes.xml",
            f'<w:footnotes {ns}><w:footnote w:id="2"><w:p><w:r><w:t>Dotyczy także cudzoziemców.</w:t></w:r></w:p></w:footnote></w:footnotes>',
        )

    text = extractor().extract(buffer.getvalue())

    assert "Termin [przypis 2: Dotyczy także cudzoziemców.]  wynosi 30 dni." in text


def test_flat_opc_notes_do_not_lose_the_main_bill() -> None:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    xml = (
        '<?xml version="1.0"?><?mso-application progid="Word.Document"?>'
        '<pkg:package xmlns:pkg="http://schemas.microsoft.com/office/2006/xmlPackage">'
        '<pkg:part pkg:name="/word/document.xml"><pkg:xmlData>'
        f"<w:document {ns}><w:body><w:p><w:r><w:t>USTAWA Art. 1.</w:t>"
        '<w:endnoteReference w:id="3"/></w:r></w:p></w:body></w:document>'
        '</pkg:xmlData></pkg:part><pkg:part pkg:name="/word/endnotes.xml"><pkg:xmlData>'
        f'<w:endnotes {ns}><w:endnote w:id="3"><w:p><w:r><w:t>Termin wynosi 60 dni.</w:t>'
        "</w:r></w:p></w:endnote></w:endnotes></pkg:xmlData></pkg:part></pkg:package>"
    ).encode()

    result = extractor().extract_document(xml)

    assert "Termin wynosi 60 dni" in result.text
    assert not result.selection.parts[0].losses


def test_missing_word_note_is_a_visible_loss_without_erasing_the_body() -> None:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document {ns}><w:body><w:p><w:r><w:t>USTAWA Art. 1.</w:t><w:footnoteReference w:id="9"/></w:r></w:p></w:body></w:document>',
        )

    result = extractor().extract_document(buffer.getvalue())

    assert "USTAWA Art. 1." in result.text
    assert result.selection.parts[0].losses == ("[nieodczytany przypis 9]",)
    assert result.selection.state != "selected"


def test_bill_support_is_not_taken_from_the_implementing_regulation() -> None:
    reasons = "UZASADNIENIE\nProjekt ustawy zmienia zasady pobytu cudzoziemców."
    wrong = "UZASADNIENIE\nProjekt rozporządzenia określa wzór formularza."
    package = archive_of(
        [
            ("Projekt ustawy.docx", word(law())),
            ("Uzasadnienie.docx", word(reasons)),
            ("TAX FREE uzasadnienie do projektu rozporzadzenia.docx", word(wrong)),
        ]
    )

    result = extractor().extract_document(package)

    assert reasons in result.text and wrong not in result.text
    assert any("supporting draft regulation" in reason for reason in result.selection.rejected)


@pytest.mark.parametrize(
    "header",
    [
        "Projekt 6.12.2023 r.",
        "`Projekt z dnia 6 grudnia 2023 r.",
        "Projekt\nz dnia 6 grudnia 2023 r.",
        "Etap: Rada Ministrów (projekt z 06.12.2023)",
        "Wersja3,0 Projekt z dnia 6 grudnia 2023 r.",
        "Projekt, 06.12.2023 r.",
        "Projekt 06 12 2023 r.",
        "Projekt ustawy z dnia 6 grudnia 2023 r.",
        "Projekt z 06 . 12 . 2023 r.",
    ],
)
def test_draft_header_dates(header: str) -> None:
    assert draft_version(header + "\n" + law().split("\n", 1)[1]) == dt.date(2023, 12, 6)


@pytest.mark.parametrize(
    "header",
    [
        "Pismo przekazujące projekt z dnia 6 grudnia 2023 r.",
        "Projekt rozporządzenia z dnia 6 grudnia 2023 r.",
        "Projekt 31.02.2023 r.",
        "Projekt 06.12.20233 r.",
        "Projekt 6.12.2023 r.\nProjekt 7.12.2023 r.",
    ],
)
def test_unrelated_invalid_or_conflicting_dates_do_not_order_editions(header: str) -> None:
    text = header + "\n" + law().split("\n", 1)[1]
    assert draft_version(text) is None


def test_a_cited_law_date_cannot_become_a_draft_date() -> None:
    assert draft_version(law().split("\n", 1)[1] + "\nProjekt 06.12.2023") is None


def test_equal_dates_with_changed_norms_remain_ambiguous() -> None:
    assert not comparable_versions([law(), law(days=60)])


def test_facts_are_bound_to_parts_and_survive_text_free_restore() -> None:
    text = law()
    part = DocumentPart(
        url="https://official.test/project",
        text=text,
        text_end=len(text),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    selected = bill_selection(DocumentSelection(parts=(part,)), source_basis=("rcl/1/stage/1",))

    assert selected.state == "selected"
    assert {fact.kind for fact in selected.facts} == {
        "official_link",
        "bill_structure",
        "draft_date",
        "coverage",
    }
    saved = DocumentSelection.model_validate_json(selected.without_text().model_dump_json())
    assert saved.state == "selected" and not saved.parts[0].text
    for fact in selected.facts:
        assert fact.quote == text[fact.start : fact.end]
    changed = selected.model_dump()
    changed["facts"][1]["quote"] = "USTAWA FOREIGN"
    with pytest.raises(ValidationError, match="source span"):
        DocumentSelection.model_validate(changed)
    changed = selected.model_dump()
    changed["facts"][0]["source_url"] = "https://other.test"
    with pytest.raises(ValidationError, match="source identity"):
        DocumentSelection.model_validate(changed)
    changed = selected.model_dump()
    changed["facts"] = changed["facts"][:1]
    with pytest.raises(ValidationError, match="four independent evidence"):
        DocumentSelection.model_validate(changed)


@pytest.mark.parametrize("reverse", [False, True])
def test_same_bills_choose_the_same_edition_in_zip_and_separate_urls(reverse: bool) -> None:
    old = law().replace("Art. 1.", "Art. 1. W ustawie z dnia 1 maja 2020 r. o pobycie (Dz. U.)")
    new = law(day=2, days=60).replace(
        "o zmianie ustawy o cudzoziemcach", "o zmianie niektórych ustaw"
    )
    new = new.replace("Art. 1.", "Art. 1. W ustawie z dnia 1 maja 2020 r. o pobycie (Dz. U.)")
    entries = [("old.docx", word(old)), ("new.docx", word(new))]
    if reverse:
        entries.reverse()
    payloads = {"https://official.test/" + name: data for name, data in entries}
    payloads["https://official.test/package"] = archive_of(entries)

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        return payloads[url]

    loader = TextLoader({"official.test": download}, extractor(), max_bytes=1_000_000)
    candidates = [
        TextDocument(kind="rcl", url=url, version_basis=("rcl/1/stage/1",))
        for url in payloads
        if not url.endswith("package")
    ]
    separate = candidates[0].model_copy(
        update={"compare_versions": True, "alternatives": tuple(candidates[1:])}
    )
    _, files = loader.select_document(separate)
    _, package = loader.select_document(
        TextDocument(
            kind="rcl", url="https://official.test/package", version_basis=("rcl/1/stage/1",)
        )
    )

    assert files.text == package.text == new
    assert files.selection.state == package.selection.state == "selected"


def test_support_cannot_supply_the_bill_date_or_silently_prove_its_applicability() -> None:
    body = law().split("\n", 1)[1]
    osr = "Projekt z dnia 1 września 2026 r.\nOCENA SKUTKÓW REGULACJI\nInna podstawa."
    parts = tuple(
        DocumentPart(
            url=f"https://official.test/{role}",
            role=role,
            text=text,
            text_end=len(text),
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        for role, text in (("bill", body), ("osr", osr))
    )
    result = bill_selection(DocumentSelection(parts=parts), source_basis=("rcl/1/stage/1",))

    assert result.state == "unverified"
    assert not any(fact.kind == "draft_date" for fact in result.facts)
    assert "support_applicability_unknown" in result.limitations
