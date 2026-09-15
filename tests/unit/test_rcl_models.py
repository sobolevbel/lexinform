"""Pure helpers of the RCL models: text selection, fingerprint, numbering."""

from datetime import date

from lexinform.models import (
    RclDocument,
    RclFolder,
    RclProject,
    RclStage,
    normalize_wykaz_number,
    rcl_fingerprint,
    rcl_number,
    rcl_project_id,
    rcl_stages,
)
from lexinform.models.rcl import StageState


def _doc(doc_id: int, name: str) -> RclDocument:
    extension = name.rsplit(".", 1)[-1]
    return RclDocument(
        id=doc_id, name=name, url=f"https://rcl.test/docs//2/1/1/1/dokument{doc_id}.{extension}"
    )


def _project(*stages: RclStage, status: str = "otwarty") -> RclProject:
    return RclProject(
        id=1,
        title="Projekt ustawy o cudzoziemcach",
        applicant="Minister Spraw Wewnętrznych i Administracji",
        created=date(2026, 9, 1),
        modified=date(2026, 9, 1),
        status=status,
        stages=stages,
    )


def _stage(number: int, name: str, state: StageState, *folders: RclFolder) -> RclStage:
    return RclStage(id=number, number=number, name=name, state=state, folders=folders)


def test_text_documents_prefer_pdf_and_skip_tables_letters_and_legacy_doc() -> None:
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "Projekt - konsultacje.docx"),
            _doc(2, "Projekt - konsultacje.pdf"),
            _doc(3, "Uzasadbnienie - konsultacje.docx"),
            _doc(4, "uzasadnienie.doc"),
            _doc(5, "OSR - konsultacje.docx"),
            _doc(6, "tabela_zgodności.DOCX"),
            _doc(7, "Pismo - konsultacje.pdf"),
        ),
    )
    project = _project(_stage(3, "Konsultacje publiczne", "reached", folder))

    picked = project.text_documents()

    assert {role: d.id for role, d in picked.items()} == {"bill": 2, "justification": 3, "osr": 5}


def test_a_file_that_calls_itself_the_bill_beats_a_nameless_pdf() -> None:
    # A note or an information sheet next to the bill must not win on its format alone; the OSR
    # is recognised with an underscore after it, not only a space.
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "Informacja.pdf"),
            _doc(2, "projekt_ustawy_o_cudzoziemcach.docx"),
            _doc(3, "OSR_do_projektu.docx"),
        ),
    )
    project = _project(_stage(3, "Konsultacje publiczne", "reached", folder))

    picked = project.text_documents()

    assert {role: d.id for role, d in picked.items()} == {"bill": 2, "osr": 3}


def test_the_latest_stage_with_a_project_folder_wins() -> None:
    early = RclFolder(id=10, name="Projekt", documents=(_doc(1, "projekt.pdf"),))
    late = RclFolder(id=20, name="Projekt", documents=(_doc(2, "projekt_po_KP.pdf"),))
    project = _project(
        _stage(3, "Konsultacje publiczne", "reached", early),
        _stage(10, "Komisja Prawnicza", "reached", late),
        _stage(12, "Rada Ministrów", "active"),
    )

    assert project.text_documents()["bill"].id == 2


def test_what_the_committees_file_beside_the_bill_is_not_taken_for_the_bill() -> None:
    # Every name here was published in a "Projekt" folder of a followed project; none of them is
    # the text, and each used to pass as one because no pattern ruled it out.
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "protokół rozbieżności - Projekt ustawy_udział PL w ETIAS (UC104).pdf"),
            _doc(2, "raport z konsultacji i opiniowania - Projekt ustawy.pdf"),
            _doc(3, "zał. 1 do raportu z konsultacji i opiniowania - Projekt ustawy.pdf"),
            _doc(4, "KSE - formatka_UC95 (30.07.2026).pdf"),
            _doc(5, "zestawienie_niewzględnionych_uwag_z_opiniowania.docx"),
            _doc(6, "akty_wykonawcze_ETIAS.zip"),
            _doc(7, "raport z uzgodnień międzyresortowych UC95.pdf"),
            _doc(8, "Projekt ustawy_udział PL w ETIAS (UC104).pdf"),
        ),
    )
    project = _project(_stage(9, "Stały Komitet Rady Ministrów", "reached", folder))

    picked = project.text_documents()

    assert {role: d.id for role, d in picked.items()} == {"bill": 8}


def test_an_appendix_to_the_osr_does_not_stand_in_for_the_osr() -> None:
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "załącznik_do_OSR_na_SKRM.docx"),
            _doc(2, "zał nr 1 Wyliczenia do OSR w zakresie nadzoru rynku.docx"),
            _doc(3, "OSR_na_SKRM.docx"),
            _doc(4, "projekt_ustawy_na_SKRM.docx"),
        ),
    )
    project = _project(_stage(9, "Stały Komitet Rady Ministrów", "reached", folder))

    picked = project.text_documents()

    assert {role: d.id for role, d in picked.items()} == {"bill": 4, "osr": 3}


def test_an_autopoprawka_and_an_opinion_are_not_the_bill_they_are_filed_against() -> None:
    # UD… on the Stały Komitet: the amendment to the government's own bill is a PDF and the bill
    # itself a package, so the format preference handed the thread to the autopoprawka.
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "autopoprawka do projektu ustawy o wspieraniu rodziny.pdf"),
            _doc(2, "KRM-0610-108-26 AUTOPOPRAWKA.pdf"),
            _doc(3, "opinia RL.zip"),
            _doc(4, "materiał uzupełniający do projektu ustawy o własności lokali.pdf"),
            _doc(5, "Projekt ustawy o zmianie ustawy o własności lokali.zip"),
        ),
    )
    project = _project(_stage(9, "Stały Komitet Rady Ministrów", "reached", folder))

    assert {role: d.id for role, d in project.text_documents().items()} == {"bill": 5}


def test_a_bill_published_for_opiniowanie_is_not_mistaken_for_an_opinion() -> None:
    folder = RclFolder(id=10, name="Projekt", documents=(_doc(1, "projekt - opiniowanie.pdf"),))

    assert _project(_stage(4, "Opiniowanie", "reached", folder)).text_documents()["bill"].id == 1


def test_a_role_tag_in_brackets_beats_the_envelope_the_name_describes() -> None:
    # One ministry files each part as an attachment to its covering letter and says which is
    # which in a tag; without the tag every one of them reads as an appendix to a letter.
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "załącznik do pismo 07.08.2026 uzgodnienia [osr].pdf"),
            _doc(2, "załącznik do pismo 07.08.2026 uzgodnienia [projekt].pdf"),
            _doc(3, "załącznik do pismo 07.08.2026 uzgodnienia [uzasadnienie].pdf"),
        ),
    )
    project = _project(_stage(2, "Uzgodnienia", "reached", folder))

    picked = project.text_documents()

    assert {role: d.id for role, d in picked.items()} == {"osr": 1, "bill": 2, "justification": 3}


def test_a_bill_ratifying_a_protocol_or_about_reporting_is_still_a_bill() -> None:
    # The patterns that rule an appendix out must not rule out a bill whose subject they name.
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "Projekt ustawy o ratyfikacji Protokołu do Konwencji.pdf"),
            _doc(2, "Projekt ustawy o raportowaniu zrównoważonego rozwoju.pdf"),
        ),
    )

    for document in folder.documents:
        picked = _project(
            _stage(
                3, "Uzgodnienia", "reached", folder.model_copy(update={"documents": (document,)})
            )
        ).text_documents()
        assert picked["bill"].id == document.id


def test_a_zip_package_counts_as_text_only_when_nothing_better_is_published() -> None:
    only_zip = RclFolder(id=10, name="Projekt", documents=(_doc(1, "Projekt ustawy.zip"),))
    with_docx = RclFolder(
        id=11, name="Projekt", documents=(_doc(2, "Projekt ustawy.zip"), _doc(3, "projekt.docx"))
    )

    zipped = _project(_stage(3, "Uzgodnienia", "reached", only_zip)).text_documents()
    mixed = _project(_stage(3, "Uzgodnienia", "reached", with_docx)).text_documents()

    assert zipped["bill"].id == 1
    assert mixed["bill"].id == 3


def test_skeleton_keeps_the_timeline_and_drops_the_folders() -> None:
    folder = RclFolder(id=10, name="Projekt", documents=(_doc(1, "projekt.pdf"),))
    project = _project(_stage(3, "Konsultacje publiczne", "reached", folder))

    skeleton = project.without_documents()

    assert [st.name for st in skeleton.stages] == [st.name for st in project.stages]
    assert skeleton.text_documents() == {} and project.text_documents() != {}
    assert len(skeleton.model_dump_json()) < len(project.model_dump_json())


def test_one_file_that_is_the_bill_and_its_uzasadnienie_is_the_bill() -> None:
    """Six of the nine projects of the corpus that ended with no bill text publish both under one
    name, and the word "uzasadnienie" in it made the whole project unreadable."""
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "Projekt ustawy+uzasadnienie+OSR_podpisane przez DP.pdf"),
            _doc(2, "Zał. 1 do OSR_Zbiorcze wyniki ankiet.pdf"),
        ),
    )
    project = _project(_stage(3, "Konsultacje publiczne", "reached", folder))

    picked = project.text_documents()

    # One file, one role: the same document handed over twice would be read (and paid for) twice.
    assert {role: d.id for role, d in picked.items()} == {"bill": 1}


def test_the_bill_filed_as_a_numbered_appendix_is_still_the_bill() -> None:
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "Załącznik nr 1 Projekt ustawy - Prawo własności przemysłowej UC81.pdf"),
            _doc(2, "Załącznik nr 2 Uzasadnienie UC81.pdf"),
            _doc(3, "TABELA ZBIEŻNOŚCI 2015_2436 UC81.docx"),
        ),
    )
    project = _project(_stage(4, "Opiniowanie", "reached", folder))

    picked = project.text_documents()

    assert {role: d.id for role, d in picked.items()} == {"bill": 1, "justification": 2}


def test_the_last_resort_never_takes_a_table_or_a_letter_for_the_bill() -> None:
    """It is reached only where nothing calls itself the bill, and there it still refuses what
    `text_role` refuses: an appendix mark is forgiven, a tabela zgodności is not."""
    folder = RclFolder(
        id=10,
        name="Projekt",
        documents=(
            _doc(1, "tabela zgodności do projektu ustawy.docx"),
            _doc(2, "Pismo przewodnie - projekt ustawy.pdf"),
            _doc(3, "autopoprawka do projektu ustawy.pdf"),
        ),
    )

    assert _project(_stage(3, "Konsultacje publiczne", "reached", folder)).text_documents() == {}


def test_no_readable_document_means_no_text() -> None:
    folder = RclFolder(id=10, name="Projekt", documents=(_doc(1, "projekt.rtf"),))

    assert _project(_stage(3, "Konsultacje publiczne", "reached", folder)).text_documents() == {}


def test_fingerprint_changes_on_stage_progress_first_documents_new_text_and_sejm() -> None:
    empty = RclFolder(id=10, name="Stanowiska zgłoszone w ramach konsultacji publicznych")
    base = _project(_stage(3, "Konsultacje publiczne", "active", empty))
    with_positions = _project(
        _stage(
            3,
            "Konsultacje publiczne",
            "active",
            empty.model_copy(update={"documents": (_doc(9, "uwagi.pdf"),)}),
        )
    )
    advanced = _project(
        _stage(3, "Konsultacje publiczne", "reached", empty),
        _stage(4, "Opiniowanie", "active"),
    )
    sent = base.model_copy(update={"rm_number": "RM-0610-1-26"})

    prints = {rcl_fingerprint(p) for p in (base, with_positions, advanced, sent)}

    assert len(prints) == 4
    assert rcl_fingerprint(base) == rcl_fingerprint(
        base.model_copy(update={"modified": date(2026, 9, 9)})
    )


def test_generic_stages_carry_number_name_and_start_date_only() -> None:
    project = _project(
        RclStage(id=2, number=2, name="Uzgodnienia", state="reached", started=date(2026, 9, 1)),
        RclStage(id=3, number=3, name="Konsultacje publiczne", state="active"),
        RclStage(id=4, number=4, name="Opiniowanie"),
    )

    stages = rcl_stages(project)

    assert [(st.stage_type, st.stage_name, st.date) for st in stages] == [
        ("RclStage", "2. Uzgodnienia", date(2026, 9, 1)),
        ("RclStage", "3. Konsultacje publiczne", None),
    ]


def test_numbering_and_wykaz_normalisation() -> None:
    assert rcl_number(12414100) == "RCL/12414100"
    assert rcl_project_id("RCL/12414100") == 12414100
    assert normalize_wykaz_number("UD 247") == "UD247"
    assert normalize_wykaz_number("  ") is None
