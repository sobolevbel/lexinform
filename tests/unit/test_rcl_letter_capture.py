import datetime as dt
from pathlib import Path
from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import ServiceUnavailableError
from lexinform.models import RclConsultation, RclLetterUseObservation, RclProject
from lexinform.models.rcl_letter_use import RclLetterUsePayload
from lexinform.services import rcl_projects
from lexinform.services.document_history import record_artifact_reads
from lexinform.services.documents import LoadedFile, TextLoader
from lexinform.services.rcl_letter_history import RclLetterUseCollector, record_rcl_letter_uses
from lexinform.services.rcl_projects import RclProjectReader
from tests.fakes import FakeRclGateway, FakeTextExtractor
from tests.harness import RCL_HOST, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_rcl_letter_use import NOW

LETTER = (
    "Warszawa, 1 października 2026 r. Prosimy o uwagi w terminie 30 dni na adres: uwagi@example.test. "
    * 5
)


def capture_reader(
    *, text: str = LETTER, url: str | None = None, physical: bool = False
) -> tuple[RclProjectReader, RclProject, RclLetterUseCollector, TextLoader]:
    document = rcl_document(737331, "Pismo.pdf", created=dt.date(2026, 10, 2))
    if url is not None:
        document = document.model_copy(update={"url": url})
    stage = rcl_stage(
        3, "Konsultacje publiczne", "active", rcl_folder(1, "Pisma kierujące", document)
    )
    project = rcl_project(stages=(stage,), consultation=None)
    gateway = FakeRclGateway()
    gateway.put(project)
    gateway.files[document.url] = docx(text) if physical else b"complete letter body"
    loader = TextLoader(
        {RCL_HOST: gateway.download, "legislacja.rcl.gov.pl": gateway.download},
        extractor() if physical else FakeTextExtractor(text),
        max_bytes=10**6,
    )
    observer = RclLetterUseCollector()
    return RclProjectReader(gateway, loader, observer=observer), project, observer, loader


def captured(observer: RclLetterUseCollector) -> RclLetterUsePayload:
    (observation,) = observer.read_rcl_letter_uses()
    payload = observation.use.payload
    assert isinstance(payload, RclLetterUsePayload), (
        "The small fixture retains its full reached facts"
    )
    return payload


def test_reader_captures_exact_input_result_and_retained_merge_context() -> None:
    reader, project, observer, loader = capture_reader()
    before = RclConsultation(deadline=dt.date(2027, 1, 1), email="kept@example.test")
    window = reader.consultation(project, known=before)
    payload = captured(observer)
    assert payload.input is not None and payload.input.chars == len(LETTER), (
        "The parser receives the original string"
    )
    assert (
        payload.result is not None and payload.after is not None and payload.before is not None
    ), "The completed merge captures both sides and its actual result"
    assert payload.result.projection.email == "uwagi@example.test"
    assert payload.after.projection.deadline == before.deadline
    assert payload.after.projection.email == before.email
    assert window is not None and window.deadline == before.deadline
    assert payload.date_argument.source == "letter.created"
    assert payload.physical.artifact_sha256 == loader.read_artifacts()[0].sha256
    assert reader.consultation(project, known=window) == window
    assert len(observer.read_rcl_letter_uses()) == 1


@pytest.mark.parametrize("source", ["stage.modified", "project.modified"])
def test_actual_date_fallback_is_recorded(
    source: Literal["stage.modified", "project.modified"],
) -> None:
    reader, project, observer, _ = capture_reader()
    stage = project.stages[0]
    folder = stage.folders[0]
    document = folder.documents[0].model_copy(update={"created": None})
    stage = stage.model_copy(
        update={
            "modified": None if source == "project.modified" else stage.modified,
            "folders": (folder.model_copy(update={"documents": (document,)}),),
        }
    )
    project = project.model_copy(update={"stages": (stage,)})
    reader.consultation(project)
    assert captured(observer).date_argument.source == source


@pytest.mark.parametrize("phase", ["read", "parse", "deadline", "merge"])
def test_fatal_failures_record_only_reached_facts_and_preserve_cause(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    reader, project, observer, loader = capture_reader()
    cause = OSError("transport cause")
    failure = ServiceUnavailableError("original failure")

    def fail(*args: object, **kwargs: object) -> None:
        raise failure from cause

    if phase == "read":
        monkeypatch.setattr(loader, "read", fail)
    else:
        monkeypatch.setattr(
            rcl_projects,
            {"parse": "parse_letter", "deadline": "deadline_of", "merge": "_better_window"}[phase],
            fail,
        )
    with pytest.raises(ServiceUnavailableError) as caught:
        reader.consultation(project)
    payload = captured(observer)
    assert caught.value is failure and failure.__cause__ is cause
    assert payload.outcome == "raised" and payload.after is None
    assert (payload.input is not None) == (phase != "read")
    assert (payload.letter_info is not None) == (phase in ("deadline", "merge"))
    assert (payload.result is not None) == (phase == "merge")


@pytest.mark.parametrize("empty", [False, True])
def test_no_text_and_nonfatal_read_failure_keep_url_only_fallback(
    monkeypatch: pytest.MonkeyPatch,
    empty: bool,
) -> None:
    reader, project, observer, loader = capture_reader(text="" if empty else LETTER)
    if not empty:

        def fail(url: str) -> LoadedFile:
            raise ValueError("read failed with private prose")

        monkeypatch.setattr(loader, "read", fail)
    window = reader.consultation(project)
    payload = captured(observer)
    assert window is not None and window.deadline is None
    assert payload.outcome == ("no_text" if empty else "read_error")
    assert payload.input is None and payload.letter_info is None
    assert "private prose" not in observer.read_rcl_letter_uses()[0].model_dump_json()


@pytest.mark.parametrize("builder", [False, True])
def test_failed_observer_and_failed_logging_do_not_change_return_or_original_exception(
    monkeypatch: pytest.MonkeyPatch,
    builder: bool,
) -> None:
    reader, project, observer, loader = capture_reader()

    def bad_observer(value: RclLetterUseObservation) -> None:
        raise KeyboardInterrupt("capture interrupted")

    def bad_log(*args: object, **kwargs: object) -> None:
        raise SystemExit(2)

    if builder:

        def bad_builder(*args: object, **kwargs: object) -> None:
            raise KeyboardInterrupt("builder interrupted")

        monkeypatch.setattr(rcl_projects, "rcl_letter_observation", bad_builder)
    else:
        monkeypatch.setattr(observer, "observe_rcl_letter_use", bad_observer)
    monkeypatch.setattr(rcl_projects.log, "warning", bad_log)
    assert reader.consultation(project) is not None
    failure = KeyboardInterrupt("business interrupted")

    def fail(url: str) -> LoadedFile:
        raise failure

    monkeypatch.setattr(loader, "read", fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        reader.consultation(project)
    assert caught.value is failure


@pytest.mark.parametrize(
    ("url", "unsafe"),
    [
        ("https://legislacja.rcl.gov.pl/letter.docx", False),
        ("https://user:SECRET@legislacja.rcl.gov.pl/letter.docx", True),
        ("https://legislacja.rcl.gov.pl/letter.docx?token=PRIVATE.docx", True),
    ],
)
def test_rcl_parent_envelope_omits_unsafe_receipt_and_keeps_exact_body_extraction(
    tmp_path: Path,
    url: str,
    unsafe: bool,
) -> None:
    reader, project, observer, loader = capture_reader(url=url, physical=True)
    reader.consultation(project)
    (observation,) = observer.read_rcl_letter_uses()
    assert observation.artifact is not None and observation.extraction is not None, (
        "The real fixture extractor returns exact physical parents"
    )
    assert (observation.read is None) == unsafe
    assert (observation.use.payload.physical.artifact_read_id is None) == unsafe
    assert observation.use.payload.physical.input_extraction_relation == "exact_output_text_sha256"
    assert (
        "SECRET" not in observation.model_dump_json()
        and "PRIVATE" not in observation.model_dump_json()
    )
    repo = SqliteBillRepository(tmp_path / "rcl-only.db")
    repo.migrate()
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW)
    assert len(repo.list_artifact_reads()) == (0 if unsafe else 1)
    assert "SECRET" not in repo.dump() and "PRIVATE" not in repo.dump()
    loaded = loader.read(url)
    assert loader.file_origins(url, loaded)[0] == observation.artifact
    assert loader.file_origins(url, LoadedFile(None, sha256="f" * 64)) == (None, None, None)
    if unsafe:
        with repo.atomic():
            record_artifact_reads(repo, loader.read_artifact_reads(), recorded_at=NOW)
        existing = repo.list_artifact_reads()
        with repo.atomic():
            record_rcl_letter_uses(repo, (observation,), recorded_at=NOW + dt.timedelta(days=1))
        assert repo.list_artifact_reads() == existing
    repo.close()
