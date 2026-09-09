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
