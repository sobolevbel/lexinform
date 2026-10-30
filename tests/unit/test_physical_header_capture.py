from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest

from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.physical_inventory import (
    MAX_PHYSICAL_METADATA_BYTES,
    HeaderRow,
    PhysicalHeaders,
    PhysicalMetadata,
    physical_sha256,
)
from lexinform.physical_header_capture import (
    PhysicalHeaderBuilder,
    PhysicalHeaderCapture,
    current_physical_header_capture,
)


def header() -> PhysicalHeaders:
    return PhysicalHeaders(
        body_sha="a" * 64,
        central_directory_sha256="b" * 64,
        central_directory_span=(10, 20),
        archive_comment=(0, None),
        rows=(),
    )


def row(name: str = "draft.docx") -> HeaderRow:
    return (name, None, 100, 50, 8, 0, 1234, 0, 0, 0, (0, None), (0, None), ())


def capture() -> PhysicalHeaderCapture:
    return PhysicalHeaderCapture(ArtifactIdentity(sha256="a" * 64, byte_size=1000), header())


def test_completed_header_preserves_the_full_legacy_payload_identity() -> None:
    source = header()
    rows = (row('żółć/"draft"\\part\n.docx'), row("second.docx"))
    builder = PhysicalHeaderBuilder(source)
    for item in rows:
        builder.append(item)
    result = builder.finish(complete=True)
    expected = PhysicalMetadata.of(source.model_copy(update={"rows": rows}))
    assert result == expected
    assert result.coverage == "complete" and result.omission is None


def test_completed_empty_header_is_distinct_from_interrupted_unknown_header() -> None:
    complete = PhysicalHeaderBuilder(header()).finish(complete=True)
    interrupted = PhysicalHeaderBuilder(header()).finish(complete=False)
    assert complete.coverage == "complete" and complete.omission is None
    assert interrupted.coverage == "partial" and interrupted.omission is not None
    assert interrupted.omission.full_metadata_sha256 is None
    assert interrupted.omission.unknown_fields == 1
    assert interrupted.metadata_id != complete.metadata_id


def test_overflow_keeps_bounded_prefix_exact_counts_and_actual_full_observation_hash() -> None:
    rows = tuple(row(f"{number:05d}-" + "ż" * 50 + ".docx") for number in range(2000))
    builder = PhysicalHeaderBuilder(header())
    for item in rows:
        builder.append(item)
    result = builder.finish(complete=True)
    assert isinstance(result.metadata, PhysicalHeaders)
    kept = result.metadata.rows
    assert 0 < len(kept) < len(rows) and kept == rows[: len(kept)]
    assert result.omission is not None and result.omission.reason == "over_limit"
    assert result.omission.omitted[0].count == len(rows) - len(kept)
    full = header().model_copy(update={"rows": rows})
    assert result.omission.full_metadata_sha256 == physical_sha256(full.model_dump(mode="json"))
    assert result.omission.unknown_fields == 0
    assert result.budget_bytes <= MAX_PHYSICAL_METADATA_BYTES


def test_one_large_row_is_observed_without_retaining_its_name() -> None:
    name = '\tż\\"' * 100000
    builder = PhysicalHeaderBuilder(header())
    builder.append(row(name))
    result = builder.finish(complete=True)
    assert isinstance(result.metadata, PhysicalHeaders) and result.metadata.rows == ()
    assert result.omission is not None and result.omission.omitted[0].count == 1
    full = header().model_copy(update={"rows": (row(name),)})
    assert result.omission.full_metadata_sha256 == physical_sha256(full.model_dump(mode="json"))
    assert name not in result.model_dump_json()


def test_interrupted_overflow_never_claims_the_unobserved_full_hash() -> None:
    builder = PhysicalHeaderBuilder(header())
    builder.append(row("a" * 40000))
    result = builder.finish(complete=False)
    assert result.omission is not None
    assert result.omission.reason == "missing_metadata"
    assert result.omission.full_metadata_sha256 is None
    assert result.omission.omitted[0].count == result.omission.unknown_fields == 1


def test_failed_row_validation_does_not_change_the_observed_prefix() -> None:
    builder = PhysicalHeaderBuilder(header())
    builder.append(row())
    with pytest.raises(UnicodeEncodeError):
        builder.append(row("bad-\ud800.docx"))
    result = builder.finish(complete=True)
    assert isinstance(result.metadata, PhysicalHeaders) and result.metadata.rows == (row(),)
    assert result.omission is not None and not result.omission.omitted
    assert result.omission.full_metadata_sha256 is None and result.omission.unknown_fields == 1


def test_closed_builder_and_reused_scope_fail_explicitly() -> None:
    observation = capture()
    with observation.scope():
        observation.builder.append(row())
    assert observation.record is not None and observation.failure is None
    with pytest.raises(ValueError, match="closed"):
        observation.builder.append(row())
    with pytest.raises(ValueError, match="only once"):
        observation.builder.finish(complete=True)
    with pytest.raises(ValueError, match="one actual call"), observation.scope():
        pass


@pytest.mark.parametrize("failure", [ValueError("parser"), KeyboardInterrupt(), SystemExit(2)])
def test_scope_keeps_original_exception_and_exposes_partial_result_after_reset(
    failure: BaseException,
) -> None:
    observation = capture()
    with pytest.raises(type(failure)) as caught, observation.scope():
        assert current_physical_header_capture() is observation
        saved = copy_context()
        observation.builder.append(row())
        raise failure
    assert caught.value is failure
    assert current_physical_header_capture() is None
    assert saved.run(current_physical_header_capture) is None
    assert observation.record is not None and observation.record.coverage == "partial"
    assert observation.failure is None


def test_nested_scope_restores_the_earlier_call_context() -> None:
    outer, inner = capture(), capture()
    with outer.scope():
        with inner.scope():
            assert current_physical_header_capture() is inner
        assert current_physical_header_capture() is outer
    assert current_physical_header_capture() is None


def test_worker_requires_its_own_scope_even_when_parent_context_is_copied() -> None:
    parent = capture()
    with parent.scope(), ThreadPoolExecutor(max_workers=1) as executor:
        copied = copy_context()
        assert executor.submit(copied.run, current_physical_header_capture).result() is None

        def own_call() -> bool:
            own = capture()
            with own.scope():
                present = current_physical_header_capture() is own
            return present and current_physical_header_capture() is None

        assert executor.submit(own_call).result()
        assert current_physical_header_capture() is parent


@pytest.mark.parametrize("finalizer_failed", [False, True])
def test_copied_context_cannot_revive_a_closed_scope_on_the_same_thread(
    monkeypatch: pytest.MonkeyPatch, finalizer_failed: bool
) -> None:
    def broken(self: PhysicalHeaderBuilder, *, complete: bool) -> PhysicalMetadata:
        raise ValueError("capture guard")

    if finalizer_failed:
        monkeypatch.setattr(PhysicalHeaderBuilder, "finish", broken)
    observation = capture()
    with observation.scope():
        saved = copy_context()
        assert saved.run(current_physical_header_capture) is observation
    assert saved.run(current_physical_header_capture) is None
    assert (observation.failure is not None) == finalizer_failed


def test_finalization_failure_is_explicit_and_does_not_mask_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: PhysicalHeaderBuilder, *, complete: bool) -> PhysicalMetadata:
        raise RuntimeError("PRIVATE capture detail")

    monkeypatch.setattr(PhysicalHeaderBuilder, "finish", broken)
    observation = capture()
    original = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught, observation.scope():
        raise original
    assert caught.value is original and current_physical_header_capture() is None
    assert observation.record is None and observation.failure is not None
    assert observation.failure.artifact == observation.artifact
    assert observation.failure.failure_class == "RuntimeError"
    assert "PRIVATE" not in str(observation.failure)


def test_successful_work_survives_capture_guard_failure_with_explicit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: PhysicalHeaderBuilder, *, complete: bool) -> PhysicalMetadata:
        raise ValueError("capture guard")

    monkeypatch.setattr(PhysicalHeaderBuilder, "finish", broken)
    observation = capture()
    with observation.scope():
        observation.builder.append(row())
    assert observation.record is None and observation.failure is not None
    assert current_physical_header_capture() is None


@pytest.mark.parametrize("work_failed", [False, True])
def test_finalization_cancellation_preserves_an_existing_exception_or_propagates_its_own(
    monkeypatch: pytest.MonkeyPatch, work_failed: bool
) -> None:
    cancellation = KeyboardInterrupt()

    def cancelled(self: PhysicalHeaderBuilder, *, complete: bool) -> PhysicalMetadata:
        raise cancellation

    monkeypatch.setattr(PhysicalHeaderBuilder, "finish", cancelled)
    observation = capture()
    original = ValueError("parser")
    expected = original if work_failed else cancellation
    with pytest.raises(type(expected)) as caught, observation.scope():
        if work_failed:
            raise original
    assert caught.value is expected and current_physical_header_capture() is None
    assert observation.failure is not None
