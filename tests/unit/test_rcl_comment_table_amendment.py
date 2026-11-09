import datetime as dt
import hashlib
from contextlib import closing
from functools import partial

import pytest

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.document_selection import RCL_COMMENT_TABLE_AMENDMENT_POLICY_VERSION
from lexinform.models import PdfPageText, PdfTextExtraction, TextDocument
from lexinform.rcl_amendments import complete_ministerial_comment_table_bundle
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_document_candidates import BILL
from tests.unit.test_physical_extraction import archive, docx

TITLE = "stanowisko Ministra Cyfryzacji wobec uwag zgłoszonych do projektu ustawy o danych (UC21)"
LETTER = (
    f"Szanowny Panie Sekretarzu, w załączeniu przekazuję tabelę zawierającą {TITLE} "
    "w ramach rozpatrywania przez Stały Komitet Rady Ministrów. "
    "Z wyrazami szacunku, przedstawiciel ministerstwa. Pismo przekazano drogą elektroniczną."
)
TABLE = (
    f"{TITLE}\nw ramach SKRM\nLp. Podmiot wnosząc\ny uwagi Jednostka redakcyjna, "
    "do której wnoszone są uwagi Zgłoszona uwaga Stanowisko\n1. Komentarz do art. 1. "
    "Uwagi dotyczą wniosku zgłoszonego podczas prac komitetu i zostały przedstawione w tabeli."
)
LETTER_PDF, TABLE_PDF = b"%PDF-1.7 test letter", b"%PDF-1.7 test table"
FIRST = "https://api.test/first.docx"
SECOND = "https://api.test/second.docx"
ATTACHMENT = "https://api.test/comments.zip"
NOW = dt.datetime(2026, 11, 9, tzinfo=dt.UTC)


class RemarksPdf(FakeTextExtractor):
    def __init__(self, letter: str, table: str, *, blank_page: bool = False) -> None:
        super().__init__("", page_count=1)
        self.letter = letter
        self.table = table
        self.blank_page = blank_page

    def pages(self, data: bytes) -> int:
        return 2 if data == TABLE_PDF and self.blank_page else 1

    def extract_pages(self, data: bytes) -> PdfTextExtraction:
        pages: tuple[PdfPageText, ...] = (
            PdfPageText(number=1, text=self.letter if data == LETTER_PDF else self.table),
        )
        if self.pages(data) == 2:
            pages += (PdfPageText(number=2),)
        return PdfTextExtraction(
            input_sha256=hashlib.sha256(data).hexdigest(),
            pypdf_version="fixture",
            pages=pages,
        )


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "wrong_project",
        "hidden_member",
        "missing_member",
        "crc",
        "amendment",
        "blank_page",
        "appended_bill",
        "verification_text_differs",
    ],
)
def test_rcl_comment_table_is_excluded_only_with_complete_positive_evidence(fault: str) -> None:
    letter, table = LETTER, TABLE
    entries = {
        "Pismo do SSKRM ws tabeli uwag.pdf": LETTER_PDF,
        "Załącznik do pisma - tabela uwag.pdf": TABLE_PDF,
    }
    if fault == "wrong_project":
        letter = letter.replace("UC21", "UC47")
    elif fault == "amendment":
        letter += "\nProponuję nadać art. 2 nowe brzmienie."
    elif fault == "appended_bill":
        table += "\nUSTAWA\nz dnia 1 marca 2026 r."
    elif fault == "hidden_member":
        entries["third.bin"] = b"unrecognized payload"
    elif fault == "missing_member":
        del entries["Załącznik do pisma - tabela uwag.pdf"]
    package = archive(entries)
    if fault == "crc":
        package = package.replace(TABLE_PDF, TABLE_PDF[:-1] + b"X", 1)
    pdf = RemarksPdf(letter, table, blank_page=fault == "blank_page")
    reader = DocumentTextExtractor(
        pdf,
        DocxTextExtractor(),
        FakeTextExtractor(""),
    )
    body = docx(BILL + "Art. 2. Przepis stosuje się w postępowaniu. " * 12)
    gateway = FakeSejmGateway(files={FIRST: body, SECOND: body, ATTACHMENT: package})
    verifier_pdf = (
        RemarksPdf(letter, table + " Zmieniona treść.")
        if fault == "verification_text_differs"
        else pdf
    )
    loader = TextLoader(
        {"api.test": gateway.download},
        reader,
        max_bytes=100_000,
        verify_rcl_comment_table=partial(complete_ministerial_comment_table_bundle, verifier_pdf),
    )
    alternative = TextDocument(url=SECOND, kind="rcl", amendment_urls=(ATTACHMENT,))
    request = TextDocument(
        url=FIRST, kind="rcl", compare_versions=True, alternatives=(alternative,)
    )
    ordinary = loader.read(ATTACHMENT)
    if fault not in {"amendment", "appended_bill"}:
        assert ordinary.text is None
    resolved, file = loader.select_document(request)

    if fault != "none":
        if fault not in {"amendment", "appended_bill"}:
            assert file.selection.state == "waiting" and file.text is None
        assert not file.excluded_amendment_reads
        assert file.selection.policy_version != RCL_COMMENT_TABLE_AMENDMENT_POLICY_VERSION
        return

    control = request.model_copy(
        update={"alternatives": (alternative.model_copy(update={"amendment_urls": ()}),)}
    )
    control_resolved, control_file = loader.select_document(control)
    assert resolved == request and resolved.url == control_resolved.url
    assert file.text == control_file.text and file.text is not None
    assert file.selection.parts == control_file.selection.parts
    assert file.selection.state == control_file.selection.state
    assert file.selection.policy_version == RCL_COMMENT_TABLE_AMENDMENT_POLICY_VERSION
    assert file.selection.rejected == control_file.selection.rejected
    (receipt,) = file.excluded_amendment_reads
    assert (
        receipt.requested_url == ATTACHMENT
        and receipt.sha256 == hashlib.sha256(package).hexdigest()
    )
    captured = loader.capture_source_read(request, resolved, file)
    assert isinstance(captured, DocumentSourceRead) and captured.document == request
    assert receipt in captured.reads
    assert captured.selection.selection.rejected == (
        *control_file.selection.rejected,
        f"{ATTACHMENT}: excluded ministerial comments package ({hashlib.sha256(package).hexdigest()})",
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        record_source_read(
            repo, captured, term=10, number="RCL/123", purpose="analysis", recorded_at=NOW
        )
        saved = repo.dump()
        record_source_read(
            repo, captured, term=10, number="RCL/123", purpose="analysis", recorded_at=NOW
        )
        assert repo.dump() == saved
        repo.restore(saved)
        assert repo.dump() == saved
