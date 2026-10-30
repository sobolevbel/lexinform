import datetime as dt
import hashlib
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from contextvars import copy_context
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.metadata_omissions import MetadataUrlOmissionCollector
from lexinform.metadata_urls import MetadataUrlDecision
from lexinform.models import (
    Attachment,
    Bill,
    DocumentSelection,
    PrintInfo,
    ProcessSummary,
    RclConsultation,
    RclProject,
    Stage,
    TextDocument,
)
from lexinform.models.source_omissions import (
    SourceMetadataOmission,
    SourceOmissionParent,
    SourceReferenceOmissionOwner,
    source_omission_canonical,
    source_omission_sha256,
)
from lexinform.sections import TextBudget
from lexinform.services import documents as document_service
from lexinform.services.analysis import AnalysisOptions, AnalysisService
from lexinform.services.document_history import (
    PhysicalSourceRead,
    SourceHistoryPair,
    record_source_read,
)
from lexinform.services.documents import TextLoader
from lexinform.services.rcl_letter_history import RclLetterHistoryPersistence
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.source_omissions import (
    SourceOmissionCollector,
    SourceOmissionInput,
    SourceOmissionPersistence,
    SourceOmissionScope,
)
from lexinform.services.sources import SejmTextSource
from lexinform.source_graph_admission import source_graph_url_decisions
from lexinform.source_omission_frames import source_omission_record
from tests.fakes import FakeLlm, FakeRclGateway, FakeSejmGateway, FakeTextExtractor, FixedClock
from tests.harness import rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_rcl_letter_capture import LETTER, capture_reader
from tests.unit.test_senate_inspection import NOW, SUBJECT, TEXT, URL, payload, repository

UNSAFE = "https://source.test/draft.docx?token=PRIVATE"
OWNER = SourceReferenceOmissionOwner(term=10, number="3039", purpose="analysis")


def test_parent_prefix_binds_full_known_manifest_without_claiming_complete_coverage() -> None:
    collector = MetadataUrlOmissionCollector()
    collector.observe("invalid_unicode", requested_url_sha256=None)
    diagnostic = collector.snapshot()
    assert diagnostic is not None, "one omitted source field produces reason accounting"
    parents = tuple(SourceOmissionParent(artifact_sha256=f"{index:064x}") for index in range(200))
    fact = source_omission_record(
        OWNER, source_view_sha256="a" * 64, diagnostics=diagnostic, parents=parents
    )
    ordered = sorted(
        parents, key=lambda item: source_omission_canonical(item.model_dump(mode="json"))
    )
    assert fact.parent_count == 200 and len(fact.parents) <= 32
    assert fact.parent_coverage == "partial" and fact.budget_bytes <= 8192
    assert fact.parents_sha256 == source_omission_sha256(
        [item.model_dump(mode="json") for item in ordered]
    )
    changed = source_omission_record(
        OWNER,
        source_view_sha256="a" * 64,
        diagnostics=diagnostic,
        parents=(*parents[:-1], SourceOmissionParent(artifact_sha256="f" * 64)),
    )
    assert changed.parents == fact.parents and changed.omission_id != fact.omission_id


def test_completed_body_before_parser_failure_keeps_original_exception_and_cause(
    repo: SqliteBillRepository,
) -> None:
    failure = ValueError("PRIVATE parser failure")
    cause = KeyError("original cause")
    failure.__cause__ = cause
    gateway = FakeSejmGateway(files={UNSAFE: b"complete body"})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(error=failure), max_bytes=1000
    )
    document = TextDocument(url=UNSAFE, kind="print")
    with pytest.raises(ValueError) as caught, loader.source_omission_scope(OWNER, document):
        loader.read(UNSAFE)
    assert caught.value is failure and failure.__cause__ is cause
    (observed,) = loader.read_source_omissions()
    assert observed.record.parent_count == 1
    assert (
        observed.record.parents[0].artifact_sha256 == hashlib.sha256(b"complete body").hexdigest()
    )
    assert observed.record.parents[0].source_relation == "unknown"
    SourceOmissionPersistence(loader.read_source_omissions).persist_source_history(
        repo, recorded_at=NOW
    )
    (saved,) = repo.list_source_metadata_omissions()
    assert saved.owner.kind == "source_reference" and repo.get(10, "3039") is None
    assert "PRIVATE" not in saved.model_dump_json()


def test_global_known_bodies_do_not_create_unobserved_owner_parents(
    repo: SqliteBillRepository,
) -> None:
    gateway = FakeSejmGateway(files={UNSAFE: b"other invocation"})
    loader = TextLoader({"source.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    loader.read(UNSAFE)
    missing = TextDocument(url="https://unknown.test/file?token=PRIVATE", kind="print")
    with pytest.raises(ValueError), loader.source_omission_scope(OWNER, missing):
        loader.read(missing.url)
    (observed,) = loader.read_source_omissions()
    assert observed.record.parent_count == 0 and not observed.artifacts
    SourceOmissionPersistence(loader.read_source_omissions).persist_source_history(
        repo, recorded_at=NOW
    )
    assert repo.get_artifact_metadata(hashlib.sha256(b"other invocation").hexdigest()) is None


def test_cache_repeat_has_same_owner_frame_and_no_extra_source_rows(
    repo: SqliteBillRepository,
) -> None:
    data = docx("USTAWA Art. 1. Cudzoziemcy. " * 40)
    gateway = FakeSejmGateway(files={UNSAFE: data})
    loader = TextLoader({"source.test": gateway.download}, extractor(), max_bytes=len(data))
    document = TextDocument(url=UNSAFE, kind="print")
    history = SourceOmissionPersistence(loader.read_source_omissions)
    for _ in range(2):
        with loader.source_omission_scope(OWNER, document):
            resolved, loaded = loader.select_document(document)
            source = loader.capture_source_read(document, resolved, loaded)
            with repo.atomic():
                record_source_read(
                    repo, source, term=10, number="3039", purpose="analysis", recorded_at=NOW
                )
    history.persist_source_history(repo, recorded_at=NOW)
    before = repo.dump()
    history.persist_source_history(repo, recorded_at=NOW + dt.timedelta(days=365))
    assert repo.dump() == before and gateway.calls == [f"download:{UNSAFE}"]
    assert len(repo.list_source_metadata_omissions()) == len(loader.read_source_omissions()) == 1
    loader.reset_read_metrics()
    assert not loader.read_source_omissions()
    history.persist_source_history(repo, recorded_at=NOW + dt.timedelta(days=366))
    assert repo.dump() == before


def test_closed_copied_context_cannot_revive_owner_capture() -> None:
    gateway = FakeSejmGateway(files={UNSAFE: b"first", UNSAFE + "2": b"second"})
    loader = TextLoader({"source.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    with loader.source_omission_scope(OWNER, TextDocument(url=UNSAFE, kind="print")):
        loader.read(UNSAFE)
        copied = copy_context()
    before = loader.read_source_omissions()
    copied.run(loader.read, UNSAFE + "2")
    assert loader.read_source_omissions() == before


def test_capture_failure_never_replaces_a_business_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = ValueError("original parser")
    gateway = FakeSejmGateway(files={UNSAFE: b"complete"})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(error=failure), max_bytes=1000
    )

    def broken(scope: SourceOmissionScope) -> None:
        raise RuntimeError("metadata failure")

    monkeypatch.setattr(SourceOmissionScope, "observation", broken)
    with (
        pytest.raises(ValueError) as caught,
        loader.source_omission_scope(OWNER, TextDocument(url=UNSAFE, kind="print")),
    ):
        loader.read(UNSAFE)
    assert caught.value is failure and failure.__cause__ is None


def test_rcl_invalid_unicode_fact_remains_owned_without_bill_enrollment(
    repo: SqliteBillRepository,
) -> None:
    url = "https://legislacja.rcl.gov.pl/letter\ud800.docx"
    reader, project, observer, loader = capture_reader(url=url, physical=True)
    result = reader.consultation(project)
    assert result is not None and result.letter_url == url
    assert not observer.read_rcl_letter_uses()
    history = SourceHistoryPair(
        RclLetterHistoryPersistence(observer),
        SourceOmissionPersistence(loader.read_source_omissions),
    )
    history.persist_source_history(repo, recorded_at=NOW)
    (fact,) = repo.list_source_metadata_omissions()
    assert fact.owner.kind == "rcl_letter" and fact.owner.project_id == project.id
    assert fact.parent_count == 1 and fact.diagnostics.total == 3
    assert fact.diagnostics.examples[0].requested_url_sha256 is None
    assert fact.owner.letter_id is not None and not repo.list_source_observations(10, "3039")
    before = repo.dump()
    repo.restore(before)
    history.persist_source_history(repo, recorded_at=NOW + dt.timedelta(days=365))
    assert repo.dump() == before


def test_senate_whole_invalid_unicode_descriptor_keeps_actual_owner_and_known_body(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    owner = repo.get(10, "1929")
    assert owner is not None, "repository helper enrolls the actual Senate owner"
    data = docx(escape(TEXT.replace("\f", "\n")))
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader({"api.sejm.gov.pl": gateway.download}, extractor(), max_bytes=len(data))
    analysis = AnalysisService(
        repo,
        SejmTextSource(gateway),
        loader,
        FakeLlm(),
        FixedClock(NOW),
        AnalysisOptions(),
        text_budget=TextBudget(60000),
    )
    template = payload()
    info = PrintInfo(
        term=10,
        number="3213",
        title=template.context.print_title,
        attachments=(
            Attachment(print_number="3213", name="resolution.docx", url=URL),
            Attachment(print_number="foreign", name="unknown.bin", url=URL + "\ud800"),
        ),
    )
    stage = Stage.model_validate(template.context.stage.model_dump())
    selected = analysis.senate_amendments_document(info, stage, titles=(SUBJECT,), owner=owner)
    assert selected is not None and gateway.calls == [f"download:{URL}"]
    history = SourceOmissionPersistence(loader.read_source_omissions)
    history.persist_source_history(repo, recorded_at=NOW)
    (fact,) = repo.list_source_metadata_omissions()
    assert fact.owner.kind == "senate" and fact.owner.number == "1929"
    assert fact.owner.resolution_number == "3213" and fact.parent_count == 1
    assert fact.diagnostics.reasons[0].reason == "invalid_unicode"
    assert fact.diagnostics.examples[0].requested_url_sha256 is None
    before = repo.dump()
    repo.close()
    assert audit(tmp_path / "history.db")["status"] == "recorded"
    repo = SqliteBillRepository(tmp_path / "history.db")
    history.persist_source_history(repo, recorded_at=NOW + dt.timedelta(days=365))
    assert repo.dump() == before
    repo.close()


def test_completed_unyielded_worker_scope_is_joined_before_caller_persistence(
    repo: SqliteBillRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    failure = ValueError("first call failed")
    urls = ("https://source.test/first?token=PRIVATE", "https://source.test/second?token=PRIVATE")
    owners = (OWNER, SourceReferenceOmissionOwner(term=10, number="4040", purpose="analysis"))

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        if url == urls[0]:
            assert started.wait(5), "the second invocation must really start before early exit"
            raise failure
        started.set()
        assert release.wait(5), "caller releases the completed but unyielded worker"
        return b"actual second body"

    loader = TextLoader({"source.test": download}, FakeTextExtractor(), max_bytes=1000)
    slots = loader.source_omission_inputs(owners)

    def step(index: int) -> None:
        with loader.source_omission_scope(
            owners[index], TextDocument(url=urls[index], kind="print")
        ):
            loader.read(urls[index])
        if index == 1:
            finished.set()

    def observed_step(index: int) -> None:
        loader.run_source_omission_input(slots[index], step, index)

    with closing(fan_out((0, 1), observed_step, workers=4)) as outcomes:
        first = next(outcomes)
        assert first.error is failure
        release.set()
    assert finished.is_set()
    caller = threading.get_ident()
    save = repo.save_source_metadata_omission
    writes: list[int] = []

    def saved(record: SourceMetadataOmission, *, recorded_at: dt.datetime) -> None:
        writes.append(threading.get_ident())
        save(record, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_source_metadata_omission", saved)
    SourceOmissionPersistence(loader.read_source_omissions).persist_source_history(
        repo, recorded_at=NOW
    )
    observed = loader.read_source_omissions()
    assert tuple(
        item.record.owner.number
        for item in observed
        if item.record.owner.kind == "source_reference"
    ) == ("3039", "4040")
    assert [item.record.parent_count for item in observed] == [0, 1]
    assert writes == [caller, caller]
    assert repo.get_artifact_metadata(hashlib.sha256(b"actual second body").hexdigest()) is not None


def test_caller_persistence_failure_rolls_back_all_new_owner_facts_and_parents(
    repo: SqliteBillRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = ("https://source.test/one?token=PRIVATE", "https://source.test/two?token=PRIVATE")
    gateway = FakeSejmGateway(files={urls[0]: b"one", urls[1]: b"two"})
    loader = TextLoader({"source.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    for index, url in enumerate(urls):
        owner = SourceReferenceOmissionOwner(term=10, number=str(3039 + index), purpose="analysis")
        with loader.source_omission_scope(owner, TextDocument(url=url, kind="print")):
            loader.read(url)
    before = repo.dump()
    save = repo.save_source_metadata_omission
    calls = 0

    def fail_second(record: SourceMetadataOmission, *, recorded_at: dt.datetime) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("storage stopped")
        save(record, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_source_metadata_omission", fail_second)
    with pytest.raises(RuntimeError, match="storage stopped"):
        SourceOmissionPersistence(loader.read_source_omissions).persist_source_history(
            repo, recorded_at=NOW
        )
    assert calls == 2 and repo.dump() == before


@pytest.mark.parametrize("hook", ["__init__", "update", "artifact", "read"])
def test_synchronous_metadata_failure_preserves_original_parser_error_and_cause(
    monkeypatch: pytest.MonkeyPatch,
    hook: str,
) -> None:
    url = "https://source.test/draft.docx"
    document = TextDocument(url=url, kind="print", extra_urls=(UNSAFE,))
    failure = ValueError("original parser")
    cause = KeyError("original cause")
    failure.__cause__ = cause
    gateway = FakeSejmGateway(files={url: b"complete body"})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(error=failure), max_bytes=1000
    )

    def stopped(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PRIVATE metadata failure")

    monkeypatch.setattr(SourceOmissionScope, hook, stopped)
    with pytest.raises(ValueError) as caught, loader.source_omission_scope(OWNER, document):
        loader.read(url)
    assert caught.value is failure and failure.__cause__ is cause
    assert not loader.read_source_omissions()
    assert loader.read_artifacts()[0].sha256 == hashlib.sha256(b"complete body").hexdigest()


@pytest.mark.parametrize(
    "hook", ["__init__", "update", "artifact", "read", "extraction", "observation"]
)
def test_synchronous_capture_failure_keeps_success_and_discards_incomplete_owner_frame(
    monkeypatch: pytest.MonkeyPatch,
    hook: str,
) -> None:
    url = "https://source.test/draft.docx"
    document = TextDocument(url=url, kind="print", extra_urls=(UNSAFE,))
    data = docx("USTAWA Art. 1. Cudzoziemcy. " * 40)
    physical = extractor()
    expected = physical.extract_document_with_provenance(data)
    gateway = FakeSejmGateway(files={url: data, UNSAFE: data})
    loader = TextLoader({"source.test": gateway.download}, physical, max_bytes=len(data))

    def stopped(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PRIVATE metadata failure")

    monkeypatch.setattr(SourceOmissionScope, hook, stopped)
    with loader.source_omission_scope(OWNER, document):
        loaded = loader.read(url)
        capture = loader.capture_source_read(document, document, loaded)
    assert loaded.text == expected.document.text
    assert not loader.read_source_omissions()
    assert isinstance(capture, PhysicalSourceRead), (
        "unsafe requested graph retains physical evidence only"
    )
    assert capture.omission is None
    assert gateway.calls == [f"download:{url}"]


def test_nested_failed_setup_masks_outer_owner_instead_of_borrowing_its_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer_url = "https://source.test/outer?token=PRIVATE"
    inner_url = "https://source.test/inner?token=PRIVATE"
    gateway = FakeSejmGateway(files={outer_url: b"outer", inner_url: b"inner"})
    loader = TextLoader({"source.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    with loader.source_omission_scope(OWNER, TextDocument(url=outer_url, kind="print")):
        loader.read(outer_url)

        def stopped(*args: object, **kwargs: object) -> None:
            raise RuntimeError("metadata setup failed")

        with monkeypatch.context() as patch:
            patch.setattr(SourceOmissionScope, "__init__", stopped)
            with loader.source_omission_scope(
                SourceReferenceOmissionOwner(term=10, number="4040", purpose="analysis"),
                TextDocument(url=inner_url, kind="print"),
            ):
                loader.read(inner_url)
    (observed,) = loader.read_source_omissions()
    assert observed.record.owner == OWNER and observed.record.parent_count == 1
    assert observed.record.parents[0].artifact_sha256 == hashlib.sha256(b"outer").hexdigest()


def test_three_rcl_project_inputs_keep_children_and_later_phase_in_actual_order() -> None:
    expected = [1, 1, 2, 2, 3, 3, 1]
    snapshots: list[tuple[str, ...]] = []
    for workers in (1, 4):
        gateway = FakeRclGateway()
        projects: list[RclProject] = []
        for project_id in (1, 2, 3):
            letters = tuple(
                rcl_document(project_id * 10 + index, "Pismo.pdf").model_copy(
                    update={
                        "url": f"https://rcl.test/project/{project_id}/letter{index}\ud800.pdf",
                    }
                )
                for index in (1, 2)
            )
            stage = rcl_stage(
                3, "Konsultacje publiczne", "active", rcl_folder(1, "Pisma kierujące", *letters)
            )
            project = rcl_project(id=project_id, stages=(stage,), consultation=None)
            projects.append(project)
            for letter in letters:
                gateway.files[letter.url] = f"actual body {project_id}/{letter.id}".encode()
        loader = TextLoader(
            {"rcl.test": gateway.download}, FakeTextExtractor(LETTER), max_bytes=1000
        )
        reader = RclProjectReader(gateway, loader)
        slots = reader.source_omission_inputs(tuple(project.id for project in projects))
        inputs = tuple(zip(projects, slots, strict=True))

        def read_project(
            item: tuple[RclProject, SourceOmissionInput | None],
            reader: RclProjectReader = reader,
        ) -> RclConsultation | None:
            return reader.run_source_omission_input(item[1], reader.consultation, item[0])

        with closing(fan_out(inputs, read_project, workers=workers)) as outcomes:
            for outcome in outcomes:
                assert outcome.result() is not None
        later = projects[0].model_copy(
            update={
                "stages": (
                    projects[0]
                    .stages[0]
                    .model_copy(
                        update={
                            "folders": (
                                rcl_folder(
                                    1,
                                    "Pisma kierujące",
                                    rcl_document(19, "Pismo.pdf").model_copy(
                                        update={"url": "https://rcl.test/project/1/later\ud800.pdf"}
                                    ),
                                ),
                            )
                        }
                    ),
                )
            }
        )
        gateway.files[later.stages[0].folders[0].documents[0].url] = b"actual later body"
        reader.consultation(later)
        observed = loader.read_source_omissions()
        assert [
            item.record.owner.project_id
            for item in observed
            if item.record.owner.kind == "rcl_letter"
        ] == expected
        snapshots.append(tuple(item.record.omission_id for item in observed))
        repo = SqliteBillRepository(":memory:")
        repo.migrate()
        SourceOmissionPersistence(loader.read_source_omissions).persist_source_history(
            repo, recorded_at=NOW
        )
        with closing(sqlite3.connect(":memory:")) as saved:
            saved.executescript(repo.dump())
            assert [
                row[0]
                for row in saved.execute(
                    "SELECT project_id FROM source_metadata_omissions ORDER BY rowid"
                )
            ] == expected
        repo.close()
    assert snapshots[0] == snapshots[1]


def test_three_senate_bill_inputs_keep_resolutions_and_later_phase_in_actual_order(
    tmp_path: Path,
) -> None:
    expected = ["1929", "1929", "1930", "1930", "1931", "1931", "1929"]
    template = payload()
    data = {
        number: docx(escape(TEXT.replace("1929", number).replace("\f", "\n")))
        for number in ("1929", "1930", "1931")
    }
    snapshots: list[tuple[str, ...]] = []
    for workers in (1, 4):
        repo = SqliteBillRepository(tmp_path / f"senate-{workers}.db")
        repo.migrate()
        gateway = FakeSejmGateway()
        for number in ("1929", "1930", "1931"):
            repo.upsert_summary(
                ProcessSummary(
                    term=10,
                    number=number,
                    title=SUBJECT,
                    document_type="projekt ustawy",
                    change_date=NOW,
                ),
                now=NOW,
            )
        owners = tuple(repo.get(10, number) for number in ("1929", "1930", "1931"))
        loader = TextLoader({"api.sejm.gov.pl": gateway.download}, extractor(), max_bytes=10**6)
        analysis = AnalysisService(
            repo,
            SejmTextSource(gateway),
            loader,
            FakeLlm(),
            FixedClock(NOW),
            AnalysisOptions(),
            text_budget=TextBudget(60000),
        )
        actual_owners = tuple(owner for owner in owners if owner is not None)
        assert len(actual_owners) == 3, "the caller enrolls all three actual Senate owners"
        slots = analysis.source_omission_inputs(actual_owners)

        def resolve(
            owner: Bill,
            gateway: FakeSejmGateway = gateway,
            analysis: AnalysisService = analysis,
        ) -> None:
            for offset in (0, 100):
                resolution = str(int(owner.number) + 1284 + offset)
                url = f"https://api.sejm.gov.pl/resolution-{resolution}.docx"
                gateway.files[url] = data[owner.number]
                info = PrintInfo(
                    term=10,
                    number=resolution,
                    title=template.context.print_title,
                    attachments=(
                        Attachment(print_number=resolution, name="resolution.docx", url=url),
                        Attachment(print_number="foreign", name="unknown.bin", url=url + "\ud800"),
                    ),
                )
                stage = Stage.model_validate(
                    {**template.context.stage.model_dump(), "print_number": resolution}
                )
                assert (
                    analysis.senate_amendments_document(info, stage, titles=(SUBJECT,), owner=owner)
                    is not None
                )

        inputs = tuple(zip(actual_owners, slots, strict=True))

        def read_resolution(
            item: tuple[Bill, SourceOmissionInput | None],
            analysis: AnalysisService = analysis,
            resolve: Callable[[Bill], None] = resolve,
        ) -> None:
            analysis.run_source_omission_input(item[1], resolve, item[0])

        with closing(fan_out(inputs, read_resolution, workers=workers)) as outcomes:
            for outcome in outcomes:
                outcome.result()
        first = actual_owners[0]
        url = "https://api.sejm.gov.pl/resolution-3999.docx"
        gateway.files[url] = data[first.number]
        info = PrintInfo(
            term=10,
            number="3999",
            title=template.context.print_title,
            attachments=(
                Attachment(print_number="3999", name="resolution.docx", url=url),
                Attachment(print_number="foreign", name="unknown.bin", url=url + "\ud800"),
            ),
        )
        stage = Stage.model_validate(
            {**template.context.stage.model_dump(), "print_number": "3999"}
        )
        analysis.senate_amendments_document(info, stage, titles=(SUBJECT,), owner=first)
        observed = loader.read_source_omissions()
        assert [
            item.record.owner.number for item in observed if item.record.owner.kind == "senate"
        ] == expected
        snapshots.append(tuple(item.record.omission_id for item in observed))
        SourceOmissionPersistence(loader.read_source_omissions).persist_source_history(
            repo, recorded_at=NOW
        )
        with closing(sqlite3.connect(":memory:")) as saved:
            saved.executescript(repo.dump())
            assert [
                row[0]
                for row in saved.execute(
                    "SELECT owner_number FROM source_metadata_omissions ORDER BY rowid"
                )
            ] == expected
        repo.close()
    assert snapshots[0] == snapshots[1]


@pytest.mark.parametrize("hook", ["reserve", "input", "child"])
def test_input_ordering_capture_failure_disables_facts_without_changing_business(
    monkeypatch: pytest.MonkeyPatch,
    hook: str,
) -> None:
    def stopped(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PRIVATE input metadata failure")

    target = SourceOmissionInput if hook == "child" else SourceOmissionCollector
    monkeypatch.setattr(target, hook, stopped)
    for parser_failure in (False, True):
        failure = ValueError("original parser")
        gateway = FakeSejmGateway(files={UNSAFE: b"complete body"})
        loader = TextLoader(
            {"source.test": gateway.download},
            FakeTextExtractor("USTAWA " * 50, error=failure if parser_failure else None),
            max_bytes=1000,
        )
        (slot,) = loader.source_omission_inputs((OWNER,))

        def read(_: int, loader: TextLoader = loader) -> str | None:
            with loader.source_omission_scope(OWNER, TextDocument(url=UNSAFE, kind="print")):
                return loader.read(UNSAFE).text

        if parser_failure:
            with pytest.raises(ValueError) as caught:
                loader.run_source_omission_input(slot, read, 0)
            assert caught.value is failure
        else:
            assert loader.run_source_omission_input(slot, read, 0) == "USTAWA " * 50
        assert not loader.read_source_omissions()
        assert gateway.calls == [f"download:{UNSAFE}"]


def test_only_new_setup_provider_failure_preserves_existing_admission_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeSejmGateway(files={UNSAFE: b"actual body"})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor("USTAWA " * 50), max_bytes=1000
    )
    document = TextDocument(url=UNSAFE, kind="print")
    original = source_graph_url_decisions
    calls = 0

    def first_failed(
        requested: TextDocument,
        resolved: TextDocument | None,
        selection: DocumentSelection | None,
        *,
        opaque_failure_prose: bool,
    ) -> Iterator[MetadataUrlDecision]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("new metadata setup failed")
        return original(requested, resolved, selection, opaque_failure_prose=opaque_failure_prose)

    monkeypatch.setattr(document_service, "source_graph_url_decisions", first_failed)
    with loader.source_omission_scope(OWNER, document):
        loaded = loader.read(UNSAFE)
        captured = loader.capture_source_read(document, document, loaded)
    assert loaded.text == "USTAWA " * 50 and calls == 2
    assert isinstance(captured, PhysicalSourceRead), (
        "existing URL admission still omits the unsafe graph"
    )
    assert captured.omission is None and not loader.read_source_omissions()
    assert gateway.calls == [f"download:{UNSAFE}"]


def test_input_cleanup_failure_preserves_success_and_original_exception_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = SourceOmissionCollector.input

    @contextmanager
    def broken_exit(
        collector: SourceOmissionCollector, slot: SourceOmissionInput | None
    ) -> Iterator[None]:
        with original(collector, slot):
            try:
                yield
            finally:
                raise RuntimeError("PRIVATE input cleanup failed")

    monkeypatch.setattr(SourceOmissionCollector, "input", broken_exit)
    for parser_failure in (False, True):
        failure = ValueError("original parser")
        cause = KeyError("original cause")
        failure.__cause__ = cause
        gateway = FakeSejmGateway(files={UNSAFE: b"actual complete body"})
        loader = TextLoader(
            {"source.test": gateway.download},
            FakeTextExtractor("USTAWA " * 50, error=failure if parser_failure else None),
            max_bytes=1000,
        )
        (slot,) = loader.source_omission_inputs((OWNER,))

        def read(_: int, loader: TextLoader = loader) -> str | None:
            with loader.source_omission_scope(OWNER, TextDocument(url=UNSAFE, kind="print")):
                return loader.read(UNSAFE).text

        if parser_failure:
            with pytest.raises(ValueError) as caught:
                loader.run_source_omission_input(slot, read, 0)
            assert caught.value is failure and failure.__cause__ is cause
        else:
            assert loader.run_source_omission_input(slot, read, 0) == "USTAWA " * 50
        assert gateway.calls == [f"download:{UNSAFE}"]
        assert len(loader.read_source_omissions()) == 1
