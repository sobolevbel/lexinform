import base64
import hashlib
from collections.abc import Callable

import pytest

from lexinform.adapters.llm_prompts import scan_note
from lexinform.models import (
    AmendmentsContext,
    BatchRequest,
    DocumentSelection,
    ScannedDocument,
    SupplementContext,
)
from lexinform.models.batch import AmendmentsQuestion, SupplementQuestion
from lexinform.ports import BatchBackend
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_senate_scan_batch import anthropic_backend, openai_backend

URL = "https://api.test/scan.pdf"
SOURCE = b"%PDF-" + b"x" * 400


class RefusingPages(FakeTextExtractor):
    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        return bytes(bytearray(data))


def loaded_scan(*, cover_letter: bool = True) -> tuple[TextLoader, ScannedDocument]:
    gateway = FakeSejmGateway(files={URL: SOURCE})
    loader = TextLoader(
        {"api.test": gateway.download}, FakeTextExtractor("", page_count=30), max_bytes=10_000
    )
    scan = loader.load_scan(URL, cover_letter=cover_letter)
    assert scan is not None, "the fake PDF has thirty readable pages and fits the byte limit"
    return loader, scan


def test_cover_and_triage_keep_original_identity_and_distinct_exact_input_hashes() -> None:
    loader, full = loaded_scan()
    triage = loader.first_pages(full, 8)

    assert triage is not None, "the scan has more than eight document pages"
    manifest = triage.scan_input
    assert manifest is not None and full.scan_input is not None, (
        "the loader creates exact manifests"
    )
    assert manifest.source_sha256 == full.sha256 == hashlib.sha256(SOURCE).hexdigest()
    assert (manifest.first_page, manifest.page_count, manifest.source_pages) == (2, 8, 30)
    assert manifest.transformations == ("cover_letter", "triage")
    assert manifest.input_sha256 == hashlib.sha256(base64.b64decode(triage.data)).hexdigest()
    assert manifest.input_sha256 != full.scan_input.input_sha256 != full.sha256
    assert ScannedDocument.model_validate_json(triage.model_dump_json()) == triage
    assert len(manifest.model_dump_json().encode()) < 512
    with pytest.raises(ValueError, match="original PDF"):
        type(manifest).model_validate({**manifest.model_dump(), "first_page": 29})
    with pytest.raises(ValueError, match="current PDF"):
        manifest.opening(b"different", 9)
    with pytest.raises(ValueError, match="attached PDF"):
        ScannedDocument.model_validate({**triage.model_dump(), "data": full.data})


def test_failed_cover_selection_reports_the_whole_file_actually_attached() -> None:
    gateway = FakeSejmGateway(files={URL: SOURCE})
    loader = TextLoader(
        {"api.test": gateway.download}, RefusingPages(page_count=30), max_bytes=10_000
    )

    scan = loader.load_scan(URL, cover_letter=True)

    assert scan is not None and scan.scan_input is not None, "the whole PDF still fits the API"
    assert (scan.pages, scan.cover_letter_pages, scan.scan_input.first_page) == (30, 0, 1)
    assert scan.scan_input.input_sha256 == scan.sha256
    assert "pominięto" not in scan_note(scan) and not scan.truncated
    assert loader.first_pages(scan, 8) is None


def test_failed_cover_selection_cannot_send_an_oversized_full_pdf() -> None:
    gateway = FakeSejmGateway(files={URL: SOURCE})
    loader = TextLoader(
        {"api.test": gateway.download}, RefusingPages(page_count=601), max_bytes=10_000
    )

    assert loader.load_scan(URL, cover_letter=True) is None


def test_legacy_scans_and_text_selection_gain_no_manufactured_manifest_or_json_field() -> None:
    raw = {"data": "JVBERi0=", "pages": 9, "of_pages": 9, "sha256": "legacy"}
    legacy = ScannedDocument.model_validate(raw)

    assert legacy.scan_input is None and "scan_input" not in legacy.model_dump()
    assert "scan_input" not in DocumentSelection().model_dump()


@pytest.mark.parametrize("factory", [openai_backend, anthropic_backend])
@pytest.mark.parametrize("supplement", [False, True])
def test_exact_metadata_preserves_provider_payload_and_estimated_price(
    factory: Callable[[], BatchBackend],
    supplement: bool,
) -> None:
    _, scan = loaded_scan()
    backend = factory()
    context: AmendmentsContext | SupplementContext = AmendmentsContext(
        number="1",
        title="Projekt",
        source_kind="print",
        text="",
        truncated=False,
        previous_summary="Opis",
        scan=scan,
    )
    if supplement:
        context = SupplementContext(
            number="1",
            title="Projekt",
            document_title="Stanowisko Rządu",
            source_kind="government_position",
            text="",
            truncated=False,
            previous_summary="Opis",
            scan=scan,
        )
    question = (
        AmendmentsQuestion(ctx=context)
        if isinstance(context, AmendmentsContext)
        else SupplementQuestion(ctx=context)
    )
    request = BatchRequest(custom_id="scan", term=10, number="1", question=question)
    legacy = request.model_copy(
        update={
            "question": question.model_copy(
                update={
                    "ctx": context.model_copy(
                        update={"scan": scan.model_copy(update={"scan_input": None})}
                    )
                }
            )
        }
    )

    prepared, old = backend.prepare_request(request), backend.prepare_request(legacy)

    assert (
        prepared.payload_json == old.payload_json
        and prepared.estimated_cost_usd == old.estimated_cost_usd
    )
    assert prepared.payload_json is not None, "the provider freezes its exact request"
    assert "scan_input" not in prepared.payload_json
