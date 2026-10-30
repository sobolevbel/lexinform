import datetime as dt
import hashlib
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.models import ProcessSummary, TextDocument
from lexinform.models.source_catalog_attempts import SourceCatalogAttempt, SourceCatalogSnapshot
from lexinform.models.source_omissions import (
    SourceMetadataOmission,
    SourceReferenceOmissionOwner,
    source_omission_sha256,
)
from lexinform.services.documents import TextLoader
from lexinform.services.source_omissions import SourceOmissionPersistence, SourceOmissionScope
from tests.fakes import FakeSejmGateway, FakeTextExtractor

NOW = dt.datetime(2026, 10, 30, tzinfo=dt.UTC)
URL = "https://source.test/draft.docx"
DOCUMENT = TextDocument(url=URL, kind="print")
OWNER = SourceReferenceOmissionOwner(term=10, number="3039", purpose="analysis")


class Cancelled(BaseException):
    pass


class CancellingExtractor(FakeTextExtractor):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def extract(self, data: bytes) -> str:
        raise self.failure


def history(loader: TextLoader) -> SourceOmissionPersistence:
    return SourceOmissionPersistence(
        loader.read_source_omissions, loader.read_source_catalog_attempts
    )


def test_actual_parser_failure_freezes_safe_view_and_known_body_without_opaque_reason(
    repo: SqliteBillRepository,
    tmp_path: Path,
) -> None:
    failure = ValueError("PRIVATE parser prose")
    cause = KeyError("PRIVATE cause")
    failure.__cause__ = cause
    gateway = FakeSejmGateway(files={URL: b"complete body"})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(error=failure), max_bytes=1000
    )
    with pytest.raises(ValueError) as caught, loader.source_omission_scope(OWNER, DOCUMENT):
        loader.read(URL)
    assert caught.value is failure and caught.value.__cause__ is cause
    assert gateway.calls == [f"download:{URL}"]
    assert not loader.read_source_omissions() and loader.read_metadata_url_omissions() is None
    (frame,) = loader.read_source_catalog_attempts()
    assert frame.attempt.error_class == "ValueError" and frame.attempt.selection == "unknown"
    assert frame.catalog.descriptors[0].document == DOCUMENT
    assert frame.attempt.parent_count == 1
    assert frame.attempt.parents[0].artifact_sha256 == hashlib.sha256(b"complete body").hexdigest()
    assert "PRIVATE" not in frame.attempt.model_dump_json()
    history(loader).persist_source_history(repo, recorded_at=NOW)
    assert repo.get(10, "3039") is None
    before = repo.dump()
    repo.restore(before)
    repo.restore(repo.dump())
    history(loader).persist_source_history(repo, recorded_at=NOW + dt.timedelta(days=365))
    assert repo.dump() == before
    path = tmp_path / "restored.db"
    restored = SqliteBillRepository(path)
    restored.restore(before)
    restored.close()
    assert audit(path)["status"] == "recorded"


def test_no_body_failure_never_borrows_the_global_inventory(repo: SqliteBillRepository) -> None:
    gateway = FakeSejmGateway(files={URL: b"prior body"})
    loader = TextLoader({"source.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    loader.read(URL)
    unknown = TextDocument(url="https://unknown.test/file", kind="print")
    with pytest.raises(ValueError), loader.source_omission_scope(OWNER, unknown):
        loader.read(unknown.url)
    (frame,) = loader.read_source_catalog_attempts()
    assert frame.attempt.parent_count == 0 and frame.attempt.selection == "unknown"
    assert not frame.artifacts and not frame.reads and not frame.extractions
    history(loader).persist_source_history(repo, recorded_at=NOW)
    assert repo.get_artifact_metadata(hashlib.sha256(b"prior body").hexdigest()) is None


def test_base_exception_retains_completed_body_and_original_object() -> None:
    failure = Cancelled("PRIVATE cancellation")
    gateway = FakeSejmGateway(files={URL: b"complete"})
    loader = TextLoader(
        {"source.test": gateway.download}, CancellingExtractor(failure), max_bytes=1000
    )
    with pytest.raises(Cancelled) as caught, loader.source_omission_scope(OWNER, DOCUMENT):
        loader.read(URL)
    assert caught.value is failure
    (frame,) = loader.read_source_catalog_attempts()
    assert frame.attempt.error_class == "Cancelled" and frame.attempt.parent_count == 1


@pytest.mark.parametrize("target", ["snapshot", "failure", "finalization"])
def test_metadata_failures_discard_capture_and_preserve_business_error(
    target: str,
) -> None:
    failure = ValueError("business failure")
    cause = KeyError("original cause")
    failure.__cause__ = cause
    gateway = FakeSejmGateway(files={URL: b"complete"})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(error=failure), max_bytes=1000
    )
    cls, method = (
        (SourceCatalogSnapshot, "of")
        if target == "snapshot"
        else (SourceOmissionScope, "read_failed" if target == "failure" else "catalog_observation")
    )
    with (
        patch.object(cls, method, side_effect=RuntimeError("metadata failure")),
        pytest.raises(ValueError) as caught,
        loader.source_omission_scope(OWNER, DOCUMENT),
    ):
        loader.read(URL)
    assert caught.value is failure and caught.value.__cause__ is cause
    assert not loader.read_source_catalog_attempts() and gateway.calls == [f"download:{URL}"]


def test_actual_selection_and_successful_cache_reads_never_create_attempts() -> None:
    gateway = FakeSejmGateway(files={URL: b"complete"})
    loader = TextLoader({"source.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    with loader.source_omission_scope(OWNER, DOCUMENT):
        loaded = loader.read(URL)
        loader.capture_source_read(DOCUMENT, DOCUMENT, loaded)
    with pytest.raises(ValueError), loader.source_omission_scope(OWNER, DOCUMENT):
        cached = loader.read(URL)
        loader.capture_source_read(DOCUMENT, DOCUMENT, cached)
        raise ValueError("later consumer failure")
    assert gateway.calls == [f"download:{URL}"] and not loader.read_source_catalog_attempts()


def test_cached_body_is_available_for_a_later_pre_return_failure_without_new_io() -> None:
    gateway = FakeSejmGateway(files={URL: b"complete"})
    loader = TextLoader({"source.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    loader.read(URL)
    with pytest.raises(ValueError), loader.source_omission_scope(OWNER, DOCUMENT):
        loader.read(URL)
        raise ValueError("selector failed before capture")
    (frame,) = loader.read_source_catalog_attempts()
    assert frame.attempt.parent_count == 1 and gateway.calls == [f"download:{URL}"]
    loader.reset_read_metrics()
    assert not loader.read_source_catalog_attempts()


def test_caller_binding_preserves_partial_parent_manifest(repo: SqliteBillRepository) -> None:
    urls = tuple(f"https://source.test/{i}" for i in range(60))
    gateway = FakeSejmGateway(files={url: str(i).encode() for i, url in enumerate(urls)})
    loader = TextLoader({"source.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    document = TextDocument(url=urls[0], kind="print", required_urls=urls[1:])
    with pytest.raises(ValueError), loader.source_omission_scope(OWNER, document):
        for url in urls:
            loader.read(url)
        raise ValueError("selector failed")
    (frame,) = loader.read_source_catalog_attempts()
    assert frame.attempt.parent_count == 60 and frame.attempt.parent_coverage == "partial"
    repo.upsert_summary(
        ProcessSummary(
            term=10, number="3039", title="Project", document_type="projekt ustawy", change_date=NOW
        ),
        now=NOW,
    )
    history(loader).persist_source_history(repo, recorded_at=NOW)
    (saved,) = repo.list_source_catalog_attempts()
    assert saved.owner.kind == "bill" and saved.parent_count == 60
    assert (
        saved.parents_sha256 == frame.attempt.parents_sha256
        and saved.parents == frame.attempt.parents
    )


@pytest.mark.parametrize("workers", [1, 4])
def test_completed_unyielded_failure_frames_write_on_caller_in_input_order(
    repo: SqliteBillRepository,
    workers: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners = tuple(OWNER.model_copy(update={"number": str(i)}) for i in (1, 2, 3))
    gateway = FakeSejmGateway(files={URL: b"body"})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(error=ValueError("parser")),
        max_bytes=1000,
    )
    slots = loader.source_omission_inputs(owners)
    started = threading.Barrier(3) if workers == 4 else None

    def failed(owner: SourceReferenceOmissionOwner) -> None:
        if started is not None:
            started.wait(timeout=5)
        with loader.source_omission_scope(owner, DOCUMENT):
            loader.read(URL)

    def run(item: tuple[SourceReferenceOmissionOwner, int]) -> None:
        loader.run_source_omission_input(slots[item[1]], failed, item[0])

    with closing(
        fan_out(tuple((owner, index) for index, owner in enumerate(owners)), run, workers=workers)
    ) as outcomes:
        next(outcomes)
    recorded: list[tuple[str, int]] = []
    save = repo.save_source_catalog_attempt

    def saved(attempt: SourceCatalogAttempt, *, recorded_at: dt.datetime) -> None:
        recorded.append((attempt.owner.number, threading.get_ident()))
        save(attempt, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_source_catalog_attempt", saved)
    history(loader).persist_source_history(repo, recorded_at=NOW)
    expected = (1, 2, 3) if workers == 4 else (1,)
    assert recorded == [(str(i), threading.get_ident()) for i in expected]


def test_positive_and_privacy_frames_keep_actual_mixed_input_order(
    repo: SqliteBillRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeSejmGateway(files={URL: b"body", URL + "?token=PRIVATE": b"body"})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(error=ValueError("parser")),
        max_bytes=1000,
    )
    for index in (1, 2, 3):
        doc = (
            DOCUMENT if index != 2 else DOCUMENT.model_copy(update={"url": URL + "?token=PRIVATE"})
        )
        with (
            pytest.raises(ValueError),
            loader.source_omission_scope(OWNER.model_copy(update={"number": str(index)}), doc),
        ):
            loader.read(doc.url)
    order: list[str] = []
    positive = repo.save_source_catalog_attempt
    private = repo.save_source_metadata_omission

    def saved(attempt: SourceCatalogAttempt, *, recorded_at: dt.datetime) -> None:
        order.append(attempt.owner.number)
        positive(attempt, recorded_at=recorded_at)

    def omitted(record: SourceMetadataOmission, *, recorded_at: dt.datetime) -> None:
        order.append("2")
        private(record, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_source_catalog_attempt", saved)
    monkeypatch.setattr(repo, "save_source_metadata_omission", omitted)
    history(loader).persist_source_history(repo, recorded_at=NOW)
    assert order == ["1", "2", "3"]


def test_failed_catalog_transaction_rolls_back_every_parent_and_shared_snapshot(
    repo: SqliteBillRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeSejmGateway(files={URL: b"body"})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(error=ValueError("parser")),
        max_bytes=1000,
    )
    for index in (1, 2):
        with (
            pytest.raises(ValueError),
            loader.source_omission_scope(OWNER.model_copy(update={"number": str(index)}), DOCUMENT),
        ):
            loader.read(URL)
    before = repo.dump()
    save = repo.save_source_catalog_attempt
    calls = 0

    def failing(attempt: SourceCatalogAttempt, *, recorded_at: dt.datetime) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("storage failed")
        save(attempt, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_source_catalog_attempt", failing)
    with pytest.raises(RuntimeError, match="storage failed"):
        history(loader).persist_source_history(repo, recorded_at=NOW)
    assert repo.dump() == before


def test_failed_nested_catalog_setup_masks_outer_owner_origins() -> None:
    other = "https://source.test/inner.docx"
    gateway = FakeSejmGateway(files={URL: b"outer", other: b"inner"})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(error=ValueError("parser")),
        max_bytes=1000,
    )
    with loader.source_omission_scope(OWNER, DOCUMENT):
        with (
            patch.object(SourceCatalogSnapshot, "of", side_effect=RuntimeError("metadata")),
            pytest.raises(ValueError),
            loader.source_omission_scope(
                OWNER.model_copy(update={"number": "other"}),
                TextDocument(url=other, kind="print"),
            ),
        ):
            loader.read(other)
        with pytest.raises(ValueError):
            loader.read(URL)
    (frame,) = loader.read_source_catalog_attempts()
    assert frame.attempt.owner == OWNER and frame.attempt.parent_count == 1
    assert frame.attempt.parents[0].artifact_sha256 == hashlib.sha256(b"outer").hexdigest()


@pytest.mark.parametrize("position", ["root", "child"])
def test_unencodable_catalog_labels_keep_actual_hash_prefix_owner_error_and_parents(
    repo: SqliteBillRepository,
    position: str,
) -> None:
    document = (
        DOCUMENT.model_copy(update={"version_basis": ("\ud800",)})
        if position == "root"
        else DOCUMENT.model_copy(
            update={
                "alternatives": (
                    TextDocument(url=URL + "/child", kind="print", version_basis=("\ud800",)),
                    TextDocument(url=URL + "/later", kind="print"),
                )
            }
        )
    )
    failure = ValueError("PRIVATE original parser")
    cause = KeyError("original cause")
    failure.__cause__ = cause
    gateway = FakeSejmGateway(files={URL: b"complete body"})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(error=failure), max_bytes=1000
    )
    with pytest.raises(ValueError) as caught, loader.source_omission_scope(OWNER, document):
        loader.read(URL)
    assert caught.value is failure and caught.value.__cause__ is cause
    (frame,) = loader.read_source_catalog_attempts()
    assert frame.catalog.source_view_sha256 == source_omission_sha256(
        document.model_dump(mode="json")
    )
    assert frame.catalog.coverage == "partial"
    assert frame.catalog.descriptor_count == (1 if position == "root" else 3)
    assert len(frame.catalog.descriptors) == (0 if position == "root" else 1)
    assert frame.attempt.owner == OWNER and frame.attempt.error_class == "ValueError"
    assert frame.attempt.parent_count == 1 and frame.attempt.selection == "unknown"
    assert not loader.read_source_omissions() and loader.read_metadata_url_omissions() is None
    history(loader).persist_source_history(repo, recorded_at=NOW)
    before = repo.dump()
    repo.restore(before)
    history(loader).persist_source_history(repo, recorded_at=NOW + dt.timedelta(days=365))
    assert repo.dump() == before and gateway.calls == [f"download:{URL}"]
