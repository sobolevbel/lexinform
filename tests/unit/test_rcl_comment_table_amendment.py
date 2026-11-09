import datetime as dt
import hashlib
from contextlib import closing
from functools import partial

import pytest

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.document_admissibility import review_admission
from lexinform.document_selection import RCL_COMMENT_TABLE_AMENDMENT_POLICY_VERSION
from lexinform.models import (
    AnalysisQuestion,
    BatchRequest,
    PdfPageText,
    PdfTextExtraction,
    TextDocument,
)
from lexinform.models.document_reads import comment_table_exclusion_note
from lexinform.rcl_amendments import complete_ministerial_comment_table_bundle
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import MAX_COMMENT_TABLE_EXCLUSIONS, TextLoader
from lexinform.services.input_receipts import capture_primary_input_receipt
from lexinform.text_projection import SourcePiece, project_pieces
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_document_candidates import BILL
from tests.unit.test_input_receipts import request as analysis_request
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
    (receipt,) = file.excluded_amendment_reads
    assert (
        receipt.requested_url == ATTACHMENT
        and receipt.sha256 == hashlib.sha256(package).hexdigest()
    )
    captured = loader.capture_source_read(request, resolved, file)
    assert isinstance(captured, DocumentSourceRead) and captured.document == request
    assert receipt in captured.reads
    assert captured.selection.selection == file.selection.without_text()
    assert file.selection.rejected == (
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


@pytest.mark.parametrize("compare_versions", [False, True])
def test_verified_comments_do_not_block_text_when_capture_is_full(compare_versions: bool) -> None:
    package = archive(
        {
            "Pismo do SSKRM ws tabeli uwag.pdf": LETTER_PDF,
            "Załącznik do pisma - tabela uwag.pdf": TABLE_PDF,
        }
    )
    urls = tuple(
        f"https://api.test/comments-{index}.zip"
        for index in range(MAX_COMMENT_TABLE_EXCLUSIONS + 1)
    )
    pdf = RemarksPdf(LETTER, TABLE)
    reader = DocumentTextExtractor(pdf, DocxTextExtractor(), FakeTextExtractor(""))
    body = docx(BILL + "Art. 2. Przepis stosuje się w postępowaniu. " * 12)
    gateway = FakeSejmGateway(files={FIRST: body, **dict.fromkeys(urls, package)})
    loader = TextLoader(
        {"api.test": gateway.download},
        reader,
        max_bytes=100_000,
        verify_rcl_comment_table=partial(complete_ministerial_comment_table_bundle, pdf),
    )
    document = TextDocument(
        url=FIRST, kind="rcl", compare_versions=compare_versions, amendment_urls=urls
    )

    resolved, loaded = (
        loader.select_document(document)
        if compare_versions
        else (document, loader.read_document(document))
    )
    assert loaded.selection.state in ("unverified", "selected") and loaded.text
    assert len(loaded.excluded_amendment_reads) == MAX_COMMENT_TABLE_EXCLUSIONS
    assert loaded.selection.policy_version == RCL_COMMENT_TABLE_AMENDMENT_POLICY_VERSION
    source = loader.capture_source_read(document, resolved, loaded)
    assert isinstance(source, DocumentSourceRead)
    assert source.selection.selection == loaded.selection.without_text()
    notes = tuple(map(comment_table_exclusion_note, loaded.excluded_amendment_reads))
    assert (
        review_admission(
            loaded.selection,
            loaded.text,
            min_chars=200,
            pages=loaded.pages,
            oversize=loaded.oversize,
            role="comparison_text",
            verified_exclusions=notes,
        )
        == "complete"
    )

    template = analysis_request(loaded.text)
    assert isinstance(template.question, AnalysisQuestion), "the fixture carries a primary request"
    ctx = template.question.ctx.model_copy(
        update={"source_kind": "rcl", "text_source": "documents"}
    )
    request = BatchRequest(
        custom_id=template.custom_id,
        term=template.term,
        number=template.number,
        question=AnalysisQuestion(kind="analysis", ctx=ctx),
    ).freeze(
        payload_json=ctx.model_dump_json(),
        model="gpt-6.1-sol",
        prompt_version="pinned",
        estimated_cost_usd=0.01,
    )
    projection = project_pieces(loaded.text, (SourcePiece(loaded.text, 0),))
    receipt = capture_primary_input_receipt(request, loaded.selection, projection, source)
    assert receipt is not None and receipt.source_selection_id == source.selection.selection_id
