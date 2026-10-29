import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, cast

import httpx2 as httpx
import pytest
from pydantic import ValidationError

from lexinform.adapters.orka import OrkaClient
from lexinform.adapters.rcl_html import RclClient
from lexinform.adapters.sejm_api import SejmApiClient, SejmApiError
from lexinform.concurrency import fan_out
from lexinform.document_downloads import DocumentDownloadRecorder
from lexinform.errors import DownloadFailureReason, SejmApiUnavailableError
from lexinform.models import DocumentDownloadDiagnostics, RunMode, RunReport
from lexinform.models.document_reads import (
    DOWNLOAD_FAILURE_OUTCOMES,
    MAX_DOWNLOAD_DIAGNOSTIC_BYTES,
)
from lexinform.ports import Downloader
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor

URL = "https://one.test/draft.pdf?token=KEEP-SECRET-OUT"
SECRET = "KEEP-SECRET-OUT"
type ClientKind = Literal["sejm", "rcl", "orka"]
type Handler = Callable[[httpx.Request], httpx.Response]


def client(
    kind: ClientKind, handler: Handler, *, retries: int = 0
) -> tuple[Downloader, Callable[[], None]]:
    transport = httpx.MockTransport(handler)
    if kind == "sejm":
        sejm = SejmApiClient(
            "https://one.test", transport=transport, max_retries=retries, sleep=lambda _: None
        )
        return sejm.download, sejm.close
    if kind == "rcl":
        rcl = RclClient(
            "https://one.test", transport=transport, max_retries=retries, sleep=lambda _: None
        )
        return rcl.download, rcl.close
    orka = OrkaClient(transport=transport, max_retries=retries, sleep=lambda _: None)
    return orka.download, orka.close


def loader(download: Downloader, *, host: str = "one.test", limit: int = 1000) -> TextLoader:
    return TextLoader({host: download}, FakeTextExtractor(), max_bytes=limit)


def diagnostics(reader: TextLoader) -> DocumentDownloadDiagnostics:
    result = reader.read_download_diagnostics()
    assert result is not None, "the test performs a measured loader event"
    return result


@pytest.mark.parametrize("kind", ["sejm", "rcl", "orka"])
@pytest.mark.parametrize("status", [404, 410])
def test_real_typed_http_status_is_preserved_without_messages(
    kind: ClientKind, status: int
) -> None:
    fetch, close = client(kind, lambda request: httpx.Response(status, content=b"missing"))
    reader = loader(fetch)
    try:
        with pytest.raises(RuntimeError):
            reader.read(URL)
        observed = diagnostics(reader)
        assert observed.invocations == observed.missing == 1
        assert observed.examples[0].status_code == status
        assert observed.examples[0].reason == "http_status"
        assert observed.examples[0].requested_url_sha256 == hashlib.sha256(URL.encode()).hexdigest()
        assert observed.examples[0].host == "one.test"
        assert (
            SECRET not in observed.model_dump_json()
            and "draft.pdf" not in observed.model_dump_json()
        )
        assert not reader.read_artifact_reads() and reader.read_metrics().body_bytes == 0
    finally:
        close()


@pytest.mark.parametrize("kind", ["sejm", "rcl", "orka"])
def test_internal_retries_are_one_loader_invocation(kind: ClientKind) -> None:
    requests: list[str] = []

    def unavailable(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(503, content=b"unavailable")

    fetch, close = client(kind, unavailable, retries=2)
    reader = loader(fetch)
    try:
        with pytest.raises(RuntimeError):
            reader.read(URL)
        observed = diagnostics(reader)
        assert len(requests) == 3 and observed.invocations == 1
        assert observed.source_unavailable == (0 if kind == "orka" else 1)
        assert observed.error == (1 if kind == "orka" else 0)
        assert observed.examples[0].status_code == 503
        assert reader.read_metrics().hosts["one.test"].attempts == 1
    finally:
        close()


def test_pre_send_budget_rejection_is_distinct_from_sent_failures() -> None:
    requests: list[str] = []

    def unavailable(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(503)

    fetch, close = client("sejm", unavailable)
    reader = loader(fetch)
    try:
        for _ in range(4):
            with pytest.raises(SejmApiUnavailableError):
                reader.read(URL)
        observed = diagnostics(reader)
        assert observed.invocations == 4 and len(requests) == 3
        assert observed.source_unavailable == 3 and observed.pre_send_rejected == 1
        assert reader.read_metrics().hosts["one.test"].failed == 4
        rejected = [group for group in observed.examples if group.outcome == "pre_send_rejected"]
        assert len(rejected) == 1 and rejected[0].status_code is None
        assert rejected[0].reason == "failure_budget"
    finally:
        close()


@pytest.mark.parametrize("kind", ["rcl", "orka"])
def test_http200_rejection_is_not_a_complete_document(kind: ClientKind) -> None:
    body = b"<html><title>Request Rejected</title>incapsula</html>"
    fetch, close = client(
        kind,
        lambda request: httpx.Response(200, content=body, headers={"content-type": "text/html"}),
    )
    reader = loader(fetch)
    try:
        with pytest.raises(RuntimeError):
            reader.read(URL)
        observed = diagnostics(reader)
        assert observed.source_unavailable == (1 if kind == "rcl" else 0)
        assert observed.refused == (1 if kind == "orka" else 0)
        assert observed.examples[0].status_code == 200
        assert observed.examples[0].reason == "rejected_response"
        assert not reader.read_artifact_reads() and not reader.read_artifacts()
    finally:
        close()


@pytest.mark.parametrize("kind", ["sejm", "rcl", "orka"])
def test_transport_error_chaining_and_messages_are_unchanged(kind: ClientKind) -> None:
    original = httpx.ConnectError(SECRET)

    def fail(request: httpx.Request) -> httpx.Response:
        raise original

    fetch, close = client(kind, fail)
    reader = loader(fetch)
    try:
        with pytest.raises(RuntimeError) as caught:
            reader.read(URL)
        assert caught.value.__cause__ is original
        assert (
            str(caught.value)
            == f"GET {URL} failed after 1 {'attempts' if kind == 'sejm' else 'attempt(s)'}: {SECRET}"
        )
        observed = diagnostics(reader)
        assert (
            observed.examples[0].reason == "transport_error"
            and observed.examples[0].status_code is None
        )
        assert SECRET not in observed.model_dump_json()
    finally:
        close()


def test_unstructured_errors_do_not_guess_http_status_from_text() -> None:
    original = ValueError("HTTP 404 " + SECRET)

    def fail(url: str, *, max_bytes: int | None = None) -> bytes:
        raise original

    reader = loader(fail)
    with pytest.raises(ValueError) as caught:
        reader.read(URL)
    assert caught.value is original
    observed = diagnostics(reader)
    assert observed.error == 1 and observed.missing == 0
    assert observed.examples[0].status_code is None and observed.examples[0].reason == "other_error"


def test_unencodable_metadata_cannot_replace_the_original_source_error() -> None:
    original = ValueError(SECRET)

    def fail(url: str, *, max_bytes: int | None = None) -> bytes:
        raise original

    reader = loader(fail)
    with pytest.raises(ValueError) as caught:
        reader.read(URL + "\ud800")
    assert caught.value is original
    observed = diagnostics(reader)
    assert observed.error == observed.omitted_failures == 1 and not observed.examples


def test_interrupted_invocation_remains_unresolved_without_an_invented_error() -> None:
    def interrupt(url: str, *, max_bytes: int | None = None) -> bytes:
        raise KeyboardInterrupt(SECRET)

    reader = loader(interrupt)
    with pytest.raises(KeyboardInterrupt):
        reader.read(URL)
    observed = diagnostics(reader)
    assert observed.unresolved == observed.invocations == 1
    assert not observed.examples and observed.error == observed.omitted_failures == 0
    assert reader.read_metrics().hosts["one.test"].attempts == 1


def test_unknown_host_invalid_url_and_cache_are_separate_from_downloader_calls() -> None:
    gateway = FakeSejmGateway(files={URL: b"body"})
    reader = loader(gateway.download)
    assert reader.read_download_diagnostics() is None
    with pytest.raises(ValueError, match="no downloader"):
        reader.read("https://unknown-secret.test/a?token=" + SECRET)
    with pytest.raises(ValueError):
        reader.read("https://[bad")
    assert not reader.read_metrics().hosts and not gateway.calls
    observed = diagnostics(reader)
    assert observed.invocations == 2 and observed.unknown_host == observed.invalid_url == 1
    assert all(group.host is None for group in observed.examples)
    reader.reset_read_metrics()
    reader.read(URL)
    reader.read(URL)
    assert diagnostics(reader).completed == diagnostics(reader).cache_hits == 1
    reader.reset_read_metrics()
    assert reader.read_download_diagnostics() is None
    reader.read(URL)
    assert diagnostics(reader).invocations == 0 and diagnostics(reader).cache_hits == 1


def test_overlimit_has_only_limit_metadata_and_cached_oversize_is_not_retried() -> None:
    gateway = FakeSejmGateway(files={URL: b"x" * 1001})
    reader = loader(gateway.download)
    assert reader.read(URL).oversize and reader.read(URL).oversize
    observed = diagnostics(reader)
    assert observed.invocations == observed.over_limit == observed.cache_hits == 1
    assert observed.examples[0].limit_bytes == 1000
    assert (
        "body_bytes" not in observed.model_dump_json()
        and '"sha256"' not in observed.model_dump_json()
    )
    assert not reader.read_artifact_reads()


def test_completed_download_survives_parser_failure_and_scan_derivatives_add_no_invocation() -> (
    None
):
    gateway = FakeSejmGateway(files={URL: b"%PDF"})
    reader = TextLoader(
        {"one.test": gateway.download},
        FakeTextExtractor(error=ValueError("broken")),
        max_bytes=1000,
    )
    with pytest.raises(ValueError, match="broken"):
        reader.read(URL)
    assert diagnostics(reader).completed == 1 and diagnostics(reader).error == 0
    assert len(reader.read_artifact_reads()) == 1
    reader = TextLoader(
        {"one.test": gateway.download}, FakeTextExtractor("", page_count=30), max_bytes=1000
    )
    scan = reader.load_scan(URL, cover_letter=False)
    assert scan is not None, "the fake PDF supplies thirty pages below the download limit"
    before = diagnostics(reader)
    reader.first_pages(scan, 8)
    assert diagnostics(reader) == before


def grouped(order: list[int], *, host: str = "one.test") -> DocumentDownloadDiagnostics:
    recorder = DocumentDownloadRecorder()
    for index in order:
        recorder.started()
        recorder.failure(f"https://one.test/{index}?token={SECRET}", host, "error", "other_error")
    result = recorder.snapshot()
    assert result is not None, "the recorder receives failed invocations"
    return result


def test_deterministic_group_eviction_retains_exact_counts_and_omissions() -> None:
    order = list(range(100)) * 3
    first, last = grouped(order), grouped(list(reversed(order)))
    assert first == last and first.error == first.invocations == 300
    assert len(first.examples) <= 8 and first.omitted_failures == 300 - sum(
        group.count for group in first.examples
    )
    assert all(group.count == 3 for group in first.examples)
    assert len(first.model_dump_json().encode()) <= MAX_DOWNLOAD_DIAGNOSTIC_BYTES
    wide = grouped(order, host="a" * 253)
    assert len(wide.examples) < 8 and wide.omitted_failures > first.omitted_failures
    assert len(wide.model_dump_json().encode()) <= 2048
    assert DocumentDownloadDiagnostics.model_validate_json(wide.model_dump_json()) == wide


@pytest.mark.parametrize("workers", [1, 4])
def test_parallel_loader_outcomes_are_deterministic(workers: int) -> None:
    def fail(url: str, *, max_bytes: int | None = None) -> bytes:
        raise ValueError(SECRET)

    reader = loader(fail)
    order = list(range(80))
    for outcome in fan_out(
        [f"https://one.test/{index}?token={SECRET}" for index in order],
        reader.read,
        workers=workers,
    ):
        with pytest.raises(ValueError):
            outcome.result()
    assert diagnostics(reader) == grouped(order)


@pytest.mark.parametrize(
    "mutation", ["count", "version", "groups", "outcome", "secret", "overflow", "size"]
)
def test_model_rejects_corrupt_or_oversized_diagnostic_metadata(mutation: str) -> None:
    value = grouped([1]).model_dump(mode="json")
    if mutation == "count":
        value["invocations"] = 2
    elif mutation == "version":
        value["schema_version"] = True
    elif mutation == "groups":
        value["examples"] *= 2
    elif mutation == "outcome":
        value["examples"][0]["outcome"] = "missing"
    elif mutation == "secret":
        value["examples"][0]["message"] = SECRET
    elif mutation == "overflow":
        value["invocations"] = 2**64
    else:
        value = grouped(list(range(16)), host="short.test").model_dump(mode="json")
        for group in value["examples"]:
            group["host"] = "a" * 253
    with pytest.raises(ValidationError):
        DocumentDownloadDiagnostics.model_validate(value)


def test_details_are_optional_typed_metadata_and_do_not_replace_exception_identity() -> None:
    original = SejmApiError(SECRET)
    assert original.with_download_details("http_status", status_code=404) is original
    assert original.args == (SECRET,) and str(original) == SECRET
    poisoned = SejmApiError(SECRET).with_download_details(cast(DownloadFailureReason, SECRET))

    def fail(url: str, *, max_bytes: int | None = None) -> bytes:
        raise poisoned

    reader = loader(fail)
    with pytest.raises(SejmApiError) as caught:
        reader.read(URL)
    assert caught.value is poisoned and diagnostics(reader).examples[0].reason == "other_error"


def test_legacy_report_and_v1_metrics_keep_their_original_json_shape() -> None:
    now = datetime(2026, 10, 28, tzinfo=UTC)
    report = RunReport(started_at=now, since=now, mode=RunMode.RUN)
    raw = report.model_dump_json()
    assert "document_download_diagnostics" not in raw
    assert RunReport.model_validate_json(raw).model_dump_json() == raw
    gateway = FakeSejmGateway(files={URL: b"body"})
    reader = loader(gateway.download)
    reader.read(URL)
    expected = {
        "schema_version": 1,
        "cache_hits": 0,
        "distinct_bodies": 1,
        "distinct_body_bytes": 4,
        "hosts": {
            "one.test": {
                "attempts": 1,
                "completed": 1,
                "body_bytes": 4,
                "over_limit": 0,
                "failed": 0,
            }
        },
    }
    assert reader.read_metrics().model_dump_json() == json.dumps(expected, separators=(",", ":"))


def test_every_failure_outcome_is_a_counted_field() -> None:
    recorder = DocumentDownloadRecorder()
    for outcome in DOWNLOAD_FAILURE_OUTCOMES:
        recorder.started()
        recorder.failure(f"https://one.test/{outcome}", "one.test", outcome, "other_error")

    report = recorder.snapshot()

    assert report is not None and report.invocations == len(DOWNLOAD_FAILURE_OUTCOMES)
    assert all(getattr(report, outcome) == 1 for outcome in DOWNLOAD_FAILURE_OUTCOMES)
    assert report.unresolved == 0 and report.completed == 0
