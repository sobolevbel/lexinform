from dataclasses import replace

import pytest

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_input import amended_input_projection, append_self_amendments
from lexinform.input_receipts import rcl_archive_self_amendment_url
from lexinform.models import AnalysisQuestion, BatchRequest, BillContext, TextDocument
from lexinform.models.input_receipts import (
    ArchiveInputPart,
    LooseRclInputPart,
    RclArchiveSelfAmendmentInputReceipt,
    input_map_sha256,
)
from lexinform.models.prepared_input import PreparedTextSegment
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.documents import TextLoader
from lexinform.services.input_receipts import capture_rcl_archive_input_receipt
from lexinform.text_projection import SourcePiece, project_pieces
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_document_candidates import BILL
from tests.unit.test_input_receipts import request
from tests.unit.test_pdf_page_records import install_pages
from tests.unit.test_physical_extraction import archive, docx


def test_hybrid_receipt_keeps_one_distinct_final_amendment_span() -> None:
    segments = (
        PreparedTextSegment(
            start=0, end=3, origin="source", part_index=0, source_start=0, source_end=3
        ),
        PreparedTextSegment(start=3, end=5, origin="generated", system_origin="input_transform"),
        PreparedTextSegment(
            start=5, end=8, origin="source", part_index=2, source_start=0, source_end=3
        ),
    )
    receipt = RclArchiveSelfAmendmentInputReceipt(
        source_selection_id="selection-v1:" + "1" * 64,
        source_observation_id="source-observation-v1:" + "2" * 64,
        extraction_id="extraction-v1:" + "3" * 64,
        source_parts=(
            ArchiveInputPart(role="bill", chars=3, text_sha256="a" * 64, physical_index=2),
            ArchiveInputPart(
                role="normative_annex", chars=3, text_sha256="b" * 64, physical_index=3
            ),
            LooseRclInputPart(
                role="self_amendment",
                chars=3,
                text_sha256="c" * 64,
                extraction_id="extraction-v1:" + "4" * 64,
            ),
        ),
        context_sha256="d" * 64,
        request_sha256="e" * 64,
        text_sha256="f" * 64,
        text_chars=8,
        full_map_sha256=input_map_sha256(segments),
        segments=segments,
    )

    assert (
        RclArchiveSelfAmendmentInputReceipt.model_validate_json(receipt.model_dump_json())
        == receipt
    )
    wrong_final = segments[-1].model_copy(update={"part_index": 1})
    wrong_map = (*segments[:-1], wrong_final)
    with pytest.raises(ValueError, match="complete final amendment"):
        RclArchiveSelfAmendmentInputReceipt.model_validate(
            {
                **receipt.model_dump(),
                "segments": wrong_map,
                "full_map_sha256": input_map_sha256(wrong_map),
            }
        )
    wrong_amendment = receipt.amendment_part.model_copy(
        update={"extraction_id": receipt.extraction_id}
    )
    with pytest.raises(ValueError, match="one distinct final loose"):
        RclArchiveSelfAmendmentInputReceipt.model_validate(
            {**receipt.model_dump(), "source_parts": (*receipt.archive_parts, wrong_amendment)}
        )


def test_hybrid_scope_accepts_only_the_exact_recorded_amendment_alternative() -> None:
    archive_url = "https://api.test/project.zip"
    amendment_url = "https://api.test/proposal.pdf"
    alternate = TextDocument(kind="rcl", url=amendment_url)
    document = TextDocument(
        kind="rcl", url=archive_url, amendment_urls=(amendment_url,), alternatives=(alternate,)
    )

    assert rcl_archive_self_amendment_url(document) == amendment_url
    for field, value in (
        ("required_urls", (amendment_url,)),
        ("amendment_urls", (archive_url,)),
        ("alternatives", (alternate.model_copy(update={"url": "https://api.test/other.pdf"}),)),
        ("alternatives", (alternate.model_copy(update={"official_link_conflict": True}),)),
    ):
        assert rcl_archive_self_amendment_url(document.model_copy(update={field: value})) is None


@pytest.mark.parametrize("missing_parent", [False, True])
def test_writer_binds_only_the_native_archive_and_separate_pdf(
    monkeypatch: pytest.MonkeyPatch,
    missing_parent: bool,
) -> None:
    archive_url = "https://api.test/project.zip"
    amendment_url = "https://api.test/proposal.pdf"
    gateway = FakeSejmGateway(
        files={
            archive_url: archive(
                {
                    "bill.docx": docx(BILL + "Art. 2. Przepis stosuje się w postępowaniu. " * 12),
                    "uzasadnienie.docx": docx("UZASADNIENIE\nOpis projektu. " * 20),
                }
            ),
            amendment_url: b"%PDF-1.7 offline fixture",
        }
    )
    install_pages(monkeypatch, ("AUTOPOPRAWKA\nProjekt poprawki. " * 20,))
    extractor = DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        FakeTextExtractor(),
    )
    loader = TextLoader({"api.test": gateway.download}, extractor, max_bytes=100_000)
    document = TextDocument(
        kind="rcl", url=archive_url, compare_versions=True, amendment_urls=(amendment_url,)
    )
    selected, loaded = loader.select_document(document)
    source = loader.capture_source_read(document, selected, loaded)
    assert isinstance(source, DocumentSourceRead), "the fixture retains both downloaded roots"
    assert loaded.text is not None, "the fixture has a readable archive main"
    amendment = loaded.selection.parts[-1]
    assert amendment.role == "self_amendment"
    combined = append_self_amendments(loaded.text, (amendment,))
    original = project_pieces(loaded.text, (SourcePiece(loaded.text, 0),))
    projection = amended_input_projection(loaded.selection, original, combined)
    assert projection is not None, "the native amendment join has exact source coordinates"
    context = request().ctx
    assert isinstance(context, BillContext), "the request fixture is a primary question"
    context = context.model_copy(update={"source_kind": "rcl", "text": combined})
    frozen = BatchRequest(
        custom_id="archive-amendment",
        term=10,
        number=context.number,
        question=AnalysisQuestion(kind="analysis", ctx=context),
    ).freeze(
        payload_json=context.model_dump_json(),
        model="gpt-6.1-sol",
        prompt_version="fixture",
        estimated_cost_usd=0,
    )
    if missing_parent:
        source = replace(
            source,
            reads=tuple(read for read in source.reads if read.requested_url != amendment_url),
        )

    receipt = capture_rcl_archive_input_receipt(frozen, loaded.selection, projection, source)

    if missing_parent:
        assert receipt is None
    else:
        assert isinstance(receipt, RclArchiveSelfAmendmentInputReceipt)
        assert receipt.request_sha256 == frozen.payload_sha256
        assert receipt.amendment_part.chars == len(amendment.text)
