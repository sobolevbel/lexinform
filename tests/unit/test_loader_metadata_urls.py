import datetime as dt
import hashlib

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import AttachmentTooLargeError
from lexinform.models import ArtifactReadIdentity
from lexinform.services.document_read_reporting import CliDocumentReadReporter
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor, FixedClock
from tests.unit.test_physical_extraction import docx, extractor

URL = "https://source.test/draft.docx"
UNSAFE = URL + "?token=PRIVATE"
BODY = b"complete body"
TEXT = "USTAWA Art. 1. Cudzoziemiec sklada wniosek. " * 30


@pytest.mark.parametrize("url", [URL, UNSAFE])
def test_real_extraction_keeps_io_text_and_url_free_origins(url: str) -> None:
    data = docx(TEXT)
    gateway = FakeSejmGateway(files={url: data})
    physical = extractor()
    expected = physical.extract_document_with_provenance(data)
    loader = TextLoader({"source.test": gateway.download}, physical, max_bytes=len(data))

    first = loader.read(url)
    again = loader.read(url)

    assert first == again and first.text == expected.document.text
    assert gateway.calls == [f"download:{url}"]
    artifact, receipt, extraction = loader.file_origins(url, first)
    assert artifact is not None, "the downloader returned a known complete body"
    assert artifact.sha256 == expected.result.artifact_sha256
    assert extraction == expected.result and url not in extraction.model_dump_json()
    assert first.selection is not None, "the physical extractor supplies a selection"
    assert loader.extractions_for_selection(first.selection) == (expected.result,)
    assert {part.url for part in first.selection.parts} == {url}
    diagnostic = loader.read_metadata_url_omissions()
    if url == URL:
        assert diagnostic is None
        assert receipt == ArtifactReadIdentity(
            sha256=artifact.sha256, byte_size=len(data), requested_url=url
        )
    else:
        assert receipt is None and not loader.read_artifact_reads()
        assert diagnostic is not None and diagnostic.total == 1
        assert diagnostic.reasons[0].reason == "unrecognized_query"
        assert diagnostic.examples[0].artifact_sha256 == artifact.sha256
        assert (
            diagnostic.examples[0].requested_url_sha256 == hashlib.sha256(url.encode()).hexdigest()
        )
        assert "PRIVATE" not in diagnostic.model_dump_json()
    assert loader.read_metrics().completed == loader.read_metrics().cache_hits == 1
    loader.reset_download_diagnostics()
    assert loader.read_metadata_url_omissions() is None
    assert loader.read(url) == first and len(loader.read_artifacts()) == 1
    assert loader.read_metadata_url_omissions() is None


@pytest.mark.parametrize("failure", [ValueError("parser failed"), RuntimeError("parser stopped")])
def test_parser_failure_keeps_body_and_omission_without_replacing_exception(
    failure: Exception,
) -> None:
    gateway = FakeSejmGateway(files={UNSAFE: BODY})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(error=failure), max_bytes=1000
    )

    with pytest.raises(type(failure)) as caught:
        loader.read(UNSAFE)

    assert caught.value is failure
    assert gateway.calls == [f"download:{UNSAFE}"]
    assert len(loader.read_artifacts()) == 1 and not loader.read_artifact_reads()
    diagnostic = loader.read_metadata_url_omissions()
    assert diagnostic is not None and diagnostic.total == 1
    assert loader.read_metrics().completed == 1


@pytest.mark.parametrize("over_limit", [False, True])
def test_failed_or_incomplete_download_has_no_body_or_url_omission(over_limit: bool) -> None:
    failure = (
        AttachmentTooLargeError(UNSAFE, 1000) if over_limit else ValueError("transport failed")
    )

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        raise failure

    loader = TextLoader({"source.test": download}, FakeTextExtractor(), max_bytes=1000)
    if over_limit:
        assert loader.read(UNSAFE).oversize
    else:
        with pytest.raises(ValueError) as caught:
            loader.read(UNSAFE)
        assert caught.value is failure
    assert not loader.read_artifacts() and not loader.read_artifact_reads()
    assert loader.read_metadata_url_omissions() is None


@pytest.mark.parametrize("parser_error", [False, True])
def test_cli_finalizer_saves_bounded_omission_and_body_after_success_or_parser_error(
    repo: SqliteBillRepository, parser_error: bool
) -> None:
    clock = FixedClock()
    failure = ValueError("PRIVATE parser detail")
    gateway = FakeSejmGateway(files={UNSAFE: BODY})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(TEXT, error=failure if parser_error else None),
        max_bytes=1000,
    )
    reporter = CliDocumentReadReporter(
        repo, clock, loader, loader, loader, loader, dt.timedelta(days=90)
    )
    try:
        with reporter.capture("scan"):
            loader.read(UNSAFE)
    except ValueError as caught:
        assert parser_error and caught is failure
    else:
        assert not parser_error

    (report,) = repo.list_runs(since=clock.now() - dt.timedelta(days=1))
    assert report.metadata_url_omissions is not None, "caller finalization retains omissions"
    assert report.metadata_url_omissions.total == 1
    assert "PRIVATE" not in report.model_dump_json()
    assert not repo.list_artifact_reads()
    assert repo.get_artifact_metadata(hashlib.sha256(BODY).hexdigest()) is not None
