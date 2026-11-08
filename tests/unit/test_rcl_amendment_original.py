import datetime as dt

import pytest

from lexinform.models.document_reads import LoadedFile
from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.models.rcl import RclProject
from lexinform.models.sejm import Attachment, PrintInfo, ProcessDetail, TextDocument
from lexinform.services.documents import TextLoader
from lexinform.services.rcl_amendment_original import read_rcl_amendment_original
from tests.fakes import FakeSejmGateway
from tests.unit.test_physical_extraction import archive, docx, extractor

RM_NUMBER = "RM-0610-73-24"
URL = "https://api.sejm.gov.pl/sejm/term10/prints/672/672-ustawa.docx"
PROPOSAL = DocumentPart(
    members=("proposal.docx",),
    text="AUTOPOPRAWKA do projektu ustawy o ochronie (druk 672)\nArt. 1. Dodaje się ust. 2.",
)


@pytest.mark.parametrize(
    ("mode", "expected_calls"),
    [
        ("complete", ("get_process:672", "get_print:672", URL)),
        ("unknown_term", ()),
        ("wrong_project", ("get_process:672",)),
        ("unconfirmed_parent", ("get_process:672", "get_print:672")),
        ("duplicate_attachment", ("get_process:672", "get_print:672")),
        ("incomplete", ("get_process:672", "get_print:672", URL)),
    ],
)
def test_original_word_attachment_requires_exact_print_and_complete_read(
    mode: str, expected_calls: tuple[str, ...]
) -> None:
    process = ProcessDetail(
        term=10,
        number="672",
        title="Rządowy projekt ustawy o ochronie",
        document_type="Projekt ustawy",
        rcl_num=RM_NUMBER if mode != "wrong_project" else "RM-0610-72-24",
        change_date=dt.datetime(2024, 9, 25, tzinfo=dt.UTC),
    )
    attachment = Attachment(print_number="672", name="672-ustawa.docx", url=URL)
    info = PrintInfo(
        term=10,
        number="672",
        title=process.title,
        official_term_confirmed=True,
        official_single_process_parent=None if mode == "unconfirmed_parent" else "672",
        attachments=(attachment,) * (2 if mode == "duplicate_attachment" else 1),
    )
    gateway = FakeSejmGateway(details={"672": process}, prints={"672": info})
    project = RclProject(
        id=12389752,
        title=process.title,
        applicant="Rada Ministrów",
        created=dt.date(2024, 9, 25),
        modified=dt.date(2024, 9, 25),
        term_label=None if mode == "unknown_term" else "X",
        rm_number=RM_NUMBER,
    )
    body_sha = "a" * 64
    text = "USTAWA\nArt. 1. Termin wynosi 30 dni."
    original = LoadedFile(
        text,
        sha256=body_sha,
        native_complete_standalone=mode != "incomplete",
        selection=DocumentSelection(
            parts=(DocumentPart(role="bill", text=text, sha256=body_sha, url=URL),)
        ),
    )

    def read(url: str) -> LoadedFile:
        gateway.calls.append(url)
        return original

    result = read_rcl_amendment_original(gateway, read, project=project, proposal=PROPOSAL)

    assert tuple(gateway.calls) == expected_calls
    assert result == ((process, original) if mode == "complete" else None)


@pytest.mark.parametrize("same_text", [True, False])
@pytest.mark.parametrize("include_support", [True, False])
def test_rcl_archive_uses_only_a_verified_original(same_text: bool, include_support: bool) -> None:
    base = "https://rcl.test/684251.zip"
    proposal = "https://rcl.test/684485.zip"
    date = dt.date(2024, 9, 25)
    project = RclProject(
        id=12389752,
        title="Projekt ustawy o ochronie",
        applicant="Rada Ministrów",
        created=date,
        modified=date,
        term_label="X",
        rm_number=RM_NUMBER,
    )
    process = ProcessDetail(
        term=10,
        number="672",
        title=project.title,
        document_type="Projekt ustawy",
        rcl_num=RM_NUMBER,
        change_date=dt.datetime(2024, 9, 25, tzinfo=dt.UTC),
    )
    info = PrintInfo(
        term=10,
        number="672",
        title=project.title,
        official_term_confirmed=True,
        official_single_process_parent="672",
        attachments=(Attachment(print_number="672", name="672-ustawa.docx", url=URL),),
    )
    body = "USTAWA\no ochronie\nArt. 1. Termin wynosi 30 dni. " * 12
    amendment = PROPOSAL.text + " Treść zmiany jest odrębna od projektu." * 12
    members = {"autopoprawka.docx": docx(amendment)}
    if include_support:
        members["uzasadnienie.docx"] = docx("UZASADNIENIE\n" + "Opis projektu. " * 50)
    gateway = FakeSejmGateway(
        details={"672": process},
        prints={"672": info},
        files={
            base: archive({"projekt ustawy.docx": docx(body)}),
            proposal: archive(members),
            URL: docx(body if same_text else body.replace("30 dni", "31 dni")),
        },
    )
    loader = TextLoader(
        {"rcl.test": gateway.download, "api.sejm.gov.pl": gateway.download},
        extractor(),
        max_bytes=100_000,
        sejm=gateway,
    )
    document = TextDocument(
        url=base,
        kind="rcl",
        compare_versions=True,
        version_basis=("rcl/12389752/stage/1", "Projekt ustawy"),
        amendment_urls=(proposal,),
        alternatives=(TextDocument(url=proposal, kind="rcl"),),
    )
    _, before = loader.select_document(document)
    selected, after = loader.select_document(document, rcl_project=project)

    assert before.selection.state == ("unverified" if include_support else "waiting")
    assert selected == document
    if same_text:
        assert after.text == loader.read(base).text
        assert tuple(part.role for part in after.selection.parts) == ("bill", "self_amendment")
        assert after.rcl_proposal is not None
    else:
        assert after == before and after.rcl_proposal is None
