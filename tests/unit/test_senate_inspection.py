import datetime as dt
import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.models import (
    ArtifactIdentity,
    ArtifactReadIdentity,
    Attachment,
    ExtractionResult,
    PrintInfo,
    ProcessSummary,
    SourceObservation,
    Stage,
    TextDocument,
)
from lexinform.models.senate_inspection import (
    SenateInspectionCandidate,
    SenateInspectionEvidence,
    SenateInspectionIdentity,
    SenateInspectionLink,
    SenateInspectionOmission,
    SenateInspectionPayload,
    SenateInspectionPhase,
    SenateInspectionRecord,
    SenateInspectionResult,
    SenateInspectionUrl,
    senate_sha256,
)
from lexinform.sections import TextBudget
from lexinform.senate_documents import inspect_senate_resolution
from lexinform.services.analysis import AnalysisOptions, AnalysisService
from lexinform.services.documents import LoadedFile, TextLoader
from lexinform.services.senate_documents import resolution_source_read
from lexinform.services.senate_history import (
    SenateInspectionCollector,
    SenateInspectionObservation,
    SenateInspectionPersistence,
    record_senate_inspections,
)
from lexinform.services.senate_inspection import SenateInspectionCapture
from lexinform.services.sources import SejmTextSource
from tests.fakes import FakeLlm, FakeSejmGateway, FixedClock
from tests.unit.test_physical_extraction import docx, extractor

NOW = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)
URL = "https://api.sejm.gov.pl/sejm/term10/prints/3213/3213-uchwala.docx"
SUBJECT = "o asystencji osobistej osób z niepełnosprawnościami"
TEXT = (Path(__file__).parents[1] / "fixtures/sejm/senate_3213_resolution.txt").read_text()


def payload(*, name: str = "3213-uchwala.docx", url: str = URL) -> SenateInspectionPayload:
    return SenateInspectionPayload.model_validate(
        {
            "owner": {"term": 10, "number": "1929"},
            "context": {
                "resolution_print": {"term": 10, "number": "3213"},
                "print_title": "Uchwała Senatu w sprawie ustawy " + SUBJECT,
                "document_date": "2026-10-14",
                "stage": {
                    "stage_type": "SenatePosition",
                    "stage_name": "Stanowisko Senatu",
                    "date": "2026-10-13",
                    "position": "wniósł poprawki",
                    "print_number": "3213",
                },
                "passed_on": "2026-09-30",
                "titles": [SUBJECT],
                "expected_subjects": [SUBJECT],
            },
            "components": {
                "resolver_sha256": "a" * 64,
                "inspection_sha256": "b" * 64,
                "python": "3.14",
            },
            "universe": [
                {
                    "descriptor": {
                        "index": 0,
                        "print_number": "3213",
                        "name": name,
                        "url": SenateInspectionUrl.of(url),
                    },
                    "inspection": {
                        "phase": "word",
                        "attempted": True,
                        "state": "inspected",
                        "reason": "qualified_text",
                        "artifact_sha256": "c" * 64,
                        "artifact_bytes": 100,
                        "pages": 0,
                        "reader_text_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
                        "word_eof": True,
                        "inspection": {
                            "limitation": "",
                            "outcome": "amendments",
                            "subject": SUBJECT,
                            "adopted_on": "2026-10-13",
                            "passed_on": "2026-09-30",
                            "complete": True,
                        },
                        "normalized_resolution_sha256": "d" * 64,
                    },
                }
            ],
            "phases": [
                {
                    "phase": "word",
                    "incomplete": False,
                    "qualified_indices": [0],
                    "equivalence_groups": ["d" * 64],
                }
            ],
            "result": {
                "outcome": "matching_equivalent_word",
                "descriptor_index": 0,
                "identity": {"sha256": "c" * 64, "subject": SUBJECT, "adopted_on": "2026-10-13"},
            },
        }
    )


def repository(tmp_path: Path, name: str = "history.db") -> SqliteBillRepository:
    repo = SqliteBillRepository(tmp_path / name)
    repo.migrate()
    repo.upsert_summary(
        ProcessSummary(
            term=10,
            number="1929",
            title="Projekt ustawy " + SUBJECT,
            document_type="projekt ustawy",
            change_date=NOW,
        ),
        now=NOW,
    )
    return repo


def known_observation(
    *, omitted: bool = False, url: str = URL, body_suffix: bytes = b""
) -> SenateInspectionObservation:
    body = docx(escape(TEXT.replace("\f", "\n"))) + body_suffix
    reader = extractor()
    requested_url = url

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        assert url == requested_url, "The physical fixture serves its exact descriptor URL"
        return body

    loader = TextLoader({"api.sejm.gov.pl": download}, reader, max_bytes=5 * 1024 * 1024)
    file = loader.read(url)
    document = TextDocument(url=url, kind="senate_amendments")
    source = resolution_source_read(
        loader, document, sha256=file.sha256, pages=file.pages, text=file.text
    )
    assert source is not None and file.sha256 is not None, (
        "The fixture returned a complete Word body"
    )
    observation = SourceObservation(
        term=10,
        number="1929",
        purpose="analysis",
        document=document,
        selection_id=source.selection.selection_id,
        recorded_at=NOW,
    )
    value = payload(name=("ą" * 6000 + ".docx") if omitted else "3213-uchwala.docx", url=url)
    item = value.universe[0]
    fact = item.inspection.model_copy(
        update={
            "artifact_sha256": file.sha256,
            "artifact_bytes": len(body),
            "reader_text_sha256": source.selection.selection.parts[0].text_sha256,
            "source_observation_id": observation.observation_id,
            "selection_id": source.selection.selection_id,
            "artifact_read_id": source.reads[0].read_id,
            "extraction_id": source.extractions[0].result_id,
        }
    )
    value = value.model_copy(
        update={
            "universe": (item.model_copy(update={"inspection": fact}),),
            "result": value.result.model_copy(
                update={
                    "identity": SenateInspectionIdentity(
                        sha256=file.sha256, subject=SUBJECT, adopted_on=dt.date(2026, 10, 13)
                    )
                }
            ),
        }
    )
    link = SenateInspectionLink(
        descriptor_index=0,
        descriptor_sha256=item.descriptor.descriptor_sha256,
        requested_url_sha256=item.descriptor.url.sha256,
        observation_id=observation.observation_id,
        selection_id=source.selection.selection_id,
        artifact_sha256=file.sha256,
        artifact_bytes=len(body),
        reader_text_sha256=fact.reader_text_sha256,
        artifact_read_id=fact.artifact_read_id,
        extraction_id=fact.extraction_id,
    )
    return SenateInspectionObservation(
        SenateInspectionRecord.of(value, (link,)), (link,), (source,)
    )


@pytest.mark.parametrize("omitted", [False, True])
def test_unknown_facts_and_full_whole_omissions_roundtrip_without_inferred_parents(
    omitted: bool,
) -> None:
    record = SenateInspectionRecord.of(
        payload(name=("ą" * 6000 + ".docx") if omitted else "3213.docx")
    )
    assert record.recorded_at is None and record.link_count == 1
    assert isinstance(record.payload, SenateInspectionOmission) is omitted
    assert record.model_validate_json(record.model_dump_json()) == record
    assert len(record.model_dump_json().encode()) <= (2048 if omitted else 8192)
    assert TEXT not in record.model_dump_json()
    assert (
        record.inspection_id
        != SenateInspectionRecord.of(payload(name="different.docx")).inspection_id
    )


@pytest.mark.parametrize(
    "url",
    [
        URL + "?token=SECRET",
        URL + "#SECRET",
        URL.replace("https://", "https://u:SECRET@"),
        "https://external.test/SECRET",
        URL.replace("/3213/", "/\nSECRET/"),
    ],
)
def test_unsafe_descriptor_urls_retain_hash_only_in_full_omission_and_link_envelopes(
    url: str,
) -> None:
    descriptor = SenateInspectionUrl.of(url)
    assert descriptor.value is None and descriptor.state == "omitted_unsafe"
    assert descriptor.sha256 == hashlib.sha256(url.encode()).hexdigest()
    for name in ("3213.docx", ("ą" * 6000 + ".docx")):
        record = SenateInspectionRecord.of(payload(name=name, url=url))
        assert "SECRET" not in record.model_dump_json()
        assert all(
            "SECRET" not in link.model_dump_json()
            for link in payload(name=name, url=url).physical_links()
        )


@pytest.mark.parametrize("omitted", [False, True])
def test_first_recording_exact_repeat_retention_and_shared_reader_roundtrip(
    tmp_path: Path, omitted: bool
) -> None:
    repo = repository(tmp_path)
    observed = known_observation(omitted=omitted)
    record_senate_inspections(repo, (observed,), recorded_at=NOW)
    before = repo.dump()
    record_senate_inspections(repo, (observed,), recorded_at=NOW + dt.timedelta(days=365))
    repo.prune_runs(before=NOW + dt.timedelta(days=366))
    repo.prune_history(
        before=NOW + dt.timedelta(days=366), memo_before=NOW + dt.timedelta(days=366)
    )
    assert repo.dump() == before
    saved = repo.get_senate_inspection(observed.record.inspection_id)
    assert saved is not None and saved.recorded_at == NOW, (
        "The explicit caller retains the first recording time"
    )
    assert list(repo.iter_senate_inspections(10, "1929")) == [saved]
    assert repo.list_senate_inspection_links(saved.inspection_id) == observed.links
    assert list(repo.iter_senate_inspections(10, "3213")) == []
    restored = SqliteBillRepository(tmp_path / "restored.db")
    restored.restore(before)
    assert (
        restored.dump() == before and restored.get_senate_inspection(saved.inspection_id) == saved
    )
    repo.close()
    assert audit(tmp_path / "history.db")["status"] == "recorded"
    restored.close()


@pytest.mark.parametrize(
    ("statement", "code"),
    [
        ("UPDATE senate_inspections SET owner_number='3213'", "senate_inspection_columns_mismatch"),
        ("UPDATE senate_inspections SET record_json='bad'", "invalid_senate_inspection"),
        (
            "UPDATE senate_inspection_sources SET descriptor_index=1",
            "senate_inspection_parent_mismatch",
        ),
        (
            "UPDATE senate_inspection_sources SET requested_url_sha256='bad'",
            "senate_inspection_parent_mismatch",
        ),
        ("DELETE FROM senate_inspection_sources", "senate_inspection_parent_mismatch"),
        ("UPDATE source_observations SET number='3213'", "senate_inspection_parent_mismatch"),
        (
            "UPDATE artifact_reads SET requested_url=requested_url || '/alias'",
            "senate_inspection_parent_mismatch",
        ),
        ("UPDATE artifacts SET byte_size=byte_size+1", "senate_inspection_parent_mismatch"),
        ("DELETE FROM selection_artifacts", "senate_inspection_parent_mismatch"),
        ("DELETE FROM selection_extractions", "senate_inspection_parent_mismatch"),
        (
            "UPDATE extraction_results SET artifact_sha256='bad'",
            "senate_inspection_parent_mismatch",
        ),
    ],
)
def test_get_doctor_and_pre_destructive_restore_share_corruption_rejection(
    tmp_path: Path, statement: str, code: str
) -> None:
    repo = repository(tmp_path)
    observed = known_observation(omitted=True)
    record_senate_inspections(repo, (observed,), recorded_at=NOW)
    valid = repo.dump()
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        db.execute(statement)
    with pytest.raises(ValueError):
        repo.get_senate_inspection(observed.record.inspection_id)
    bad = repo.dump()
    repo.close()
    bytes_before = (tmp_path / "history.db").read_bytes()
    report = audit(tmp_path / "history.db")
    assert code in {finding["code"] for finding in report["findings"]}
    assert (tmp_path / "history.db").read_bytes() == bytes_before
    target = SqliteBillRepository(tmp_path / "target.db")
    target.restore(valid)
    with pytest.raises(ValueError):
        target.restore(bad)
    assert target.dump() == valid
    target.close()


def test_missing_known_owner_and_changed_opaque_fact_cannot_be_saved(tmp_path: Path) -> None:
    repo = SqliteBillRepository(tmp_path / "unknown.db")
    repo.migrate()
    record = SenateInspectionRecord.of(payload(name=("ą" * 6000 + ".docx")))
    with pytest.raises(ValueError, match="known Bill"):
        repo.save_senate_inspection(
            record, payload(name=("ą" * 6000 + ".docx")).physical_links(), recorded_at=NOW
        )
    repo.close()
    repo = repository(tmp_path)
    links = payload(name=("ą" * 6000 + ".docx")).physical_links()
    record_senate_inspections(repo, (SenateInspectionObservation(record, links),), recorded_at=NOW)
    assert isinstance(record.payload, SenateInspectionOmission), (
        "The fixture exceeds the full byte cap"
    )
    changed = record.model_copy(
        update={"payload": record.payload.model_copy(update={"decision_sha256": "f" * 64})}
    )
    before = repo.dump()
    with pytest.raises(ValueError, match="immutable"):
        repo.save_senate_inspection(changed, links, recorded_at=NOW)
    assert repo.dump() == before
    repo.close()


@pytest.mark.parametrize("workers", [1, 4])
def test_clock_free_collector_preserves_snapshot_and_writer_uses_calling_thread(
    tmp_path: Path, workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = SenateInspectionCollector()
    observations = [
        SenateInspectionObservation(
            SenateInspectionRecord.of(payload(name=name)), payload(name=name).physical_links()
        )
        for name in ("3213.docx", "second.docx")
    ]
    outcomes = list(fan_out(observations * 3, collector.observe, workers=workers))
    assert all(outcome.error is None for outcome in outcomes)
    snapshot = collector.snapshot()
    assert {item.record.inspection_id for item in snapshot} == {
        item.record.inspection_id for item in observations
    }
    repo = repository(tmp_path)
    threads: list[int] = []
    save = repo.save_senate_inspection

    def record(
        value: SenateInspectionRecord,
        links: tuple[SenateInspectionLink, ...] = (),
        *,
        recorded_at: dt.datetime,
    ) -> None:
        threads.append(threading.get_ident())
        save(value, links, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_senate_inspection", record)
    record_senate_inspections(repo, snapshot, recorded_at=NOW)
    assert threads == [threading.get_ident()] * 2
    assert collector.snapshot() == snapshot
    repo.close()


def test_parent_and_inspection_write_failure_rolls_back_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path)
    before = repo.dump()
    observed = known_observation()

    def fail(
        value: SenateInspectionRecord,
        links: tuple[SenateInspectionLink, ...] = (),
        *,
        recorded_at: dt.datetime,
    ) -> None:
        raise sqlite3.OperationalError("injected persistence refusal")

    monkeypatch.setattr(repo, "save_senate_inspection", fail)
    with pytest.raises(sqlite3.OperationalError):
        record_senate_inspections(repo, (observed,), recorded_at=NOW)
    assert repo.dump() == before
    repo.close()


def test_source_link_manifest_and_actual_candidate_identity_are_exact() -> None:
    observed = known_observation()
    link = observed.links[0]
    for changed in (
        link.model_copy(update={"artifact_bytes": link.artifact_bytes + 1}),
        link.model_copy(update={"descriptor_index": 1}),
        link.model_copy(update={"extraction_id": "extraction-v1:" + "e" * 64}),
    ):
        with pytest.raises(ValueError):
            observed.record.verify_links((changed,))
    with pytest.raises(ValueError):
        observed.record.verify_links(())
    with pytest.raises(ValueError):
        replace(observed, sources=()).validated()


def test_empty_universe_no_choice_and_partial_outage_are_explicit() -> None:
    base = payload()
    empty = base.model_copy(
        update={
            "universe": (),
            "phases": (
                SenateInspectionPhase(
                    phase="word", incomplete=False, qualified_indices=(), equivalence_groups=()
                ),
                SenateInspectionPhase(
                    phase="pdf", incomplete=False, qualified_indices=(), equivalence_groups=()
                ),
            ),
            "result": SenateInspectionResult(
                outcome="all_rejected", descriptor_index=None, identity=None
            ),
        }
    )
    assert SenateInspectionRecord.of(empty).descriptor_count == 0
    first = base.universe[0]
    missing = first.inspection.model_copy(
        update={
            "reason": "no_resolution_heading",
            "inspection": SenateInspectionEvidence(
                limitation="no_resolution_heading",
                outcome=None,
                subject="",
                adopted_on=None,
                passed_on=None,
                complete=False,
            ),
            "normalized_resolution_sha256": None,
        }
    )
    failed = SenateInspectionCandidate.model_validate(
        {
            "descriptor": {
                "index": 1,
                "print_number": "3213",
                "name": "3213.pdf",
                "url": SenateInspectionUrl.of(URL + ".pdf"),
            },
            "inspection": {
                "phase": "pdf",
                "attempted": True,
                "state": "read_error",
                "reason": "source_unavailable",
                "error_class": "ServiceUnavailableError",
            },
        }
    )
    later = failed.model_copy(
        update={
            "descriptor": failed.descriptor.model_copy(update={"index": 2}),
            "inspection": failed.inspection.model_copy(
                update={
                    "phase": "filter",
                    "attempted": False,
                    "state": "unattempted",
                    "reason": "after_outage",
                    "error_class": None,
                }
            ),
        }
    )
    partial = base.model_copy(
        update={
            "universe": (first.model_copy(update={"inspection": missing}), failed, later),
            "phases": (
                SenateInspectionPhase(
                    phase="word", incomplete=False, qualified_indices=(), equivalence_groups=()
                ),
            ),
            "result": SenateInspectionResult(
                outcome="propagated_failure",
                descriptor_index=None,
                identity=None,
                error_class="ServiceUnavailableError",
            ),
        }
    )
    record = SenateInspectionRecord.of(partial)
    assert record.descriptor_count == 3
    assert isinstance(record.payload, SenateInspectionPayload), (
        "The finite partial context fits the full cap"
    )
    assert record.payload.universe[0].inspection.artifact_sha256 is not None
    assert record.payload.universe[1].inspection.artifact_sha256 is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.model_copy(
            update={
                "universe": (
                    value.universe[0].model_copy(
                        update={
                            "descriptor": value.universe[0].descriptor.model_copy(
                                update={"index": 1}
                            )
                        }
                    ),
                )
            }
        ),
        lambda value: value.model_copy(
            update={
                "phases": (value.phases[0].model_copy(update={"equivalence_groups": ("e" * 64,)}),)
            }
        ),
    ],
)
def test_incomplete_universe_and_conflicting_phase_result_facts_are_rejected(
    mutate: Callable[[SenateInspectionPayload], SenateInspectionPayload],
) -> None:
    with pytest.raises(ValueError):
        SenateInspectionRecord.of(mutate(payload()))


def test_schema_sixty_prefix_and_sql_byte_caps_are_preserved(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    assert SCHEMA_VERSION == len(MIGRATIONS) == 61
    repo.close()
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        for state, count in (("complete", 4097), ("omitted_over_limit", 1025)):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                db.execute(
                    "INSERT INTO senate_inspections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "test",
                        "a" * 64,
                        10,
                        "1929",
                        10,
                        "3213",
                        state,
                        0,
                        0,
                        "b" * 64,
                        NOW.isoformat(),
                        "ą" * count,
                    ),
                )


@pytest.mark.parametrize("schema", [60, 61])
def test_missing_table_is_legacy_unavailable_or_current_corruption(
    tmp_path: Path, schema: int
) -> None:
    repo = repository(tmp_path)
    repo.close()
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        db.execute("DROP TABLE senate_inspection_sources")
        db.execute("DROP TABLE senate_inspections")
        db.execute(f"PRAGMA user_version={schema}")
    report = audit(tmp_path / "history.db")
    assert report["status"] == ("not_run" if schema == 60 else "failed")
    assert not report["senate_inspections_available"]


def test_empty_current_reader_is_available_without_claiming_inspection_history(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    repo.close()
    report = audit(tmp_path / "history.db")
    assert report["status"] == "not_run" and report["senate_inspections_available"]


def test_whole_omission_cannot_drop_known_body_or_source_links() -> None:
    value = payload(name="ą" * 6000 + ".docx")
    with pytest.raises(ValueError, match="complete candidate body"):
        SenateInspectionRecord.of(value, ())


def test_later_known_parents_produce_a_new_immutable_fact(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    known = known_observation()
    assert isinstance(known.record.payload, SenateInspectionPayload), (
        "The finite known graph fits the full cap"
    )
    value = known.record.payload
    item = value.universe[0]
    unknown_fact = item.inspection.model_copy(
        update={
            "artifact_read_id": None,
            "extraction_id": None,
            "source_observation_id": None,
            "selection_id": None,
        }
    )
    unknown_payload = value.model_copy(
        update={"universe": (item.model_copy(update={"inspection": unknown_fact}),)}
    )
    unknown = SenateInspectionObservation(
        SenateInspectionRecord.of(unknown_payload), unknown_payload.physical_links()
    )
    record_senate_inspections(repo, (unknown,), recorded_at=NOW)
    before = repo.get_senate_inspection(unknown.record.inspection_id)
    record_senate_inspections(repo, (known,), recorded_at=NOW + dt.timedelta(days=1))
    assert known.record.inspection_id != unknown.record.inspection_id
    assert repo.get_senate_inspection(unknown.record.inspection_id) == before
    assert len(list(repo.iter_senate_inspections(10, "1929"))) == 2
    repo.close()


def test_credential_query_does_not_leak_into_new_record_or_junction(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    observed = known_observation(omitted=True, url=URL + "?token=SECRET")
    record_senate_inspections(repo, (observed,), recorded_at=NOW)
    saved = repo.get_senate_inspection(observed.record.inspection_id)
    assert saved is not None, "The explicit fixture writer saved its known parents and record"
    assert "SECRET" not in saved.model_dump_json()
    assert all(
        "SECRET" not in link.model_dump_json()
        for link in repo.list_senate_inspection_links(saved.inspection_id)
    )
    repo.close()
    with closing(sqlite3.connect(tmp_path / "history.db")) as db:
        row = db.execute("SELECT * FROM senate_inspection_sources").fetchone()
        assert "SECRET" not in json.dumps(row)


def test_orphan_junction_is_rejected_even_when_all_inspection_rows_are_missing(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    observed = known_observation()
    record_senate_inspections(repo, (observed,), recorded_at=NOW)
    valid = repo.dump()
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        db.execute("DELETE FROM senate_inspections")
    with pytest.raises(ValueError):
        repo.get_senate_inspection(observed.record.inspection_id)
    corrupt = repo.dump()
    repo.close()
    report = audit(tmp_path / "history.db")
    assert "senate_inspection_orphan_link" in {item["code"] for item in report["findings"]}
    target = SqliteBillRepository(tmp_path / "target.db")
    target.restore(valid)
    with pytest.raises(ValueError, match="no recorded parent"):
        target.restore(corrupt)
    assert target.dump() == valid
    target.close()


@pytest.mark.parametrize("kind", ["none", "visual", "conflict"])
def test_no_choice_visual_and_conflicting_pdf_facts_retain_the_complete_universe(
    tmp_path: Path, kind: str
) -> None:
    base = payload()
    original = base.universe[0]
    first = original.model_copy(
        update={
            "descriptor": original.descriptor.model_copy(update={"name": "3213.pdf"}),
            "inspection": original.inspection.model_copy(
                update={"phase": "pdf", "word_eof": False, "pages": 3}
            ),
        }
    )
    candidates: tuple[SenateInspectionCandidate, ...] = (first,)
    word = SenateInspectionPhase(
        phase="word", incomplete=False, qualified_indices=(), equivalence_groups=()
    )
    if kind == "conflict":
        second = first.model_copy(
            update={
                "descriptor": first.descriptor.model_copy(update={"index": 1, "name": "other.pdf"}),
                "inspection": first.inspection.model_copy(
                    update={"normalized_resolution_sha256": "e" * 64}
                ),
            }
        )
        candidates = (first, second)
        pdf = SenateInspectionPhase(
            phase="pdf",
            incomplete=False,
            qualified_indices=(0, 1),
            equivalence_groups=("d" * 64, "e" * 64),
        )
        result = SenateInspectionResult(
            outcome="conflicting_competing_phase", descriptor_index=None, identity=None
        )
    else:
        rejected = first.inspection.model_copy(
            update={
                "reason": "no_resolution_heading",
                "inspection": SenateInspectionEvidence(
                    limitation="no_resolution_heading",
                    outcome=None,
                    subject="",
                    adopted_on=None,
                    passed_on=None,
                    complete=False,
                ),
                "normalized_resolution_sha256": None,
                "visual_eligible": kind == "visual",
            }
        )
        candidates = (first.model_copy(update={"inspection": rejected}),)
        pdf = SenateInspectionPhase(
            phase="pdf", incomplete=False, qualified_indices=(), equivalence_groups=()
        )
        result = (
            SenateInspectionResult(
                outcome="unique_visual_fallback",
                descriptor_index=0,
                identity=SenateInspectionIdentity(
                    sha256="c" * 64,
                    subject=SUBJECT,
                    adopted_on=dt.date(2026, 10, 13),
                    scan=True,
                    pages=3,
                ),
            )
            if kind == "visual"
            else SenateInspectionResult(
                outcome="all_rejected", descriptor_index=None, identity=None
            )
        )
    value = base.model_copy(
        update={"universe": candidates, "phases": (word, pdf), "result": result}
    )
    value = SenateInspectionPayload.model_validate_json(value.model_dump_json())
    observation = SenateInspectionObservation(
        SenateInspectionRecord.of(value), value.physical_links()
    )
    repo = repository(tmp_path)
    record_senate_inspections(repo, (observation,), recorded_at=NOW)
    saved = repo.get_senate_inspection(observation.record.inspection_id)
    assert saved is not None, "The explicit caller stores the finite completed candidate history"
    assert isinstance(saved.payload, SenateInspectionPayload), "The finite family fits the full cap"
    assert saved.payload.result == result and saved.payload.universe == candidates
    assert all(
        link.observation_id is None and link.extraction_id is None
        for link in repo.list_senate_inspection_links(saved.inspection_id)
    )
    repo.close()


@pytest.mark.parametrize("omitted", [False, True])
def test_later_different_body_under_same_url_cannot_replace_an_earlier_inspection_parent(
    tmp_path: Path, omitted: bool
) -> None:
    earlier = known_observation()
    later = known_observation(body_suffix=b"later-complete-response")
    assert earlier.links[0].artifact_sha256 != later.links[0].artifact_sha256
    assert earlier.links[0].reader_text_sha256 == later.links[0].reader_text_sha256
    assert isinstance(earlier.record.payload, SenateInspectionPayload), (
        "The earlier finite context retains its exact loaded body"
    )
    value = earlier.record.payload
    item = value.universe[0]
    later_link = later.links[0]
    descriptor = (
        item.descriptor.model_copy(update={"name": "ą" * 6000 + ".docx"})
        if omitted
        else item.descriptor
    )
    wrong_fact = item.inspection.model_copy(
        update={
            "source_observation_id": later_link.observation_id,
            "selection_id": later_link.selection_id,
            "artifact_read_id": later_link.artifact_read_id,
            "extraction_id": later_link.extraction_id,
        }
    )
    wrong_payload = value.model_copy(
        update={
            "universe": (
                item.model_copy(update={"descriptor": descriptor, "inspection": wrong_fact}),
            )
        }
    )
    wrong = SenateInspectionObservation(
        SenateInspectionRecord.of(wrong_payload), wrong_payload.physical_links(), later.sources
    )
    repo = repository(tmp_path)
    record_senate_inspections(repo, (earlier,), recorded_at=NOW)
    before = repo.dump()
    with pytest.raises(ValueError, match="exact requested body"):
        record_senate_inspections(repo, (wrong,), recorded_at=NOW)
    assert repo.dump() == before
    repo.close()


@pytest.mark.parametrize("foreign", ["", "foreign / not-a-number", "ą" * 6000])
def test_foreign_descriptor_number_is_preserved_without_an_invented_numbering_grammar(
    foreign: str,
) -> None:
    base = payload()
    other = SenateInspectionCandidate.model_validate(
        {
            "descriptor": {
                "index": 1,
                "print_number": foreign,
                "name": "foreign.pdf",
                "url": SenateInspectionUrl.of(URL + ".foreign"),
            },
            "inspection": {
                "phase": "filter",
                "attempted": False,
                "state": "unattempted",
                "reason": "foreign_print",
            },
        }
    )
    value = base.model_copy(update={"universe": (*base.universe, other)})
    record = SenateInspectionRecord.of(value)
    if isinstance(record.payload, SenateInspectionPayload):
        assert record.payload.universe[1].descriptor.print_number == foreign
    else:
        assert record.payload.descriptor_count == 2 and record.coverage == "omitted_over_limit"
    assert record.link_count == 1


@pytest.mark.parametrize("name", [".pdf", ".docx"])
def test_unsupported_dotfile_descriptor_facts_roundtrip(name: str) -> None:
    base = payload()
    dotfile = SenateInspectionCandidate.model_validate(
        {
            "descriptor": {
                "index": 1,
                "print_number": "3213",
                "name": name,
                "url": SenateInspectionUrl.of(URL + "/" + name),
            },
            "inspection": {
                "phase": "filter",
                "attempted": False,
                "state": "unattempted",
                "reason": "unsupported_extension",
            },
        }
    )
    value = base.model_copy(update={"universe": (*base.universe, dotfile)})
    record = SenateInspectionRecord.of(value)
    assert record.coverage == "complete" and record.descriptor_count == 2


@pytest.mark.parametrize("drop_phases", [False, True])
def test_reader_preserves_truthful_terminal_results_without_replaying_resolver_policy(
    drop_phases: bool,
) -> None:
    base = payload()
    value = base.model_copy(
        update={
            "result": SenateInspectionResult(
                outcome="all_rejected", descriptor_index=None, identity=None
            ),
            "phases": () if drop_phases else base.phases,
        }
    )
    record = SenateInspectionRecord.of(value)
    assert isinstance(record.payload, SenateInspectionPayload), (
        "The finite captured facts fit the full record"
    )
    assert record.payload.universe[0].inspection.reason == "qualified_text"
    assert record.payload.result.outcome == "all_rejected"


def test_collector_and_sql_are_deterministic_for_opposite_completion_orders(tmp_path: Path) -> None:
    observations = tuple(
        SenateInspectionObservation(SenateInspectionRecord.of(value), value.physical_links())
        for value in (payload(name="first.docx"), payload(name="second.docx"))
    )
    snapshots: list[tuple[SenateInspectionObservation, ...]] = []
    dumps: list[str] = []
    for index, order in enumerate((observations, observations[::-1])):
        collector = SenateInspectionCollector()
        for item in order:
            collector.observe(item)
        snapshots.append(collector.snapshot())
        repo = repository(tmp_path, f"order-{index}.db")
        record_senate_inspections(repo, collector.snapshot(), recorded_at=NOW)
        dumps.append(repo.dump())
        repo.close()
    assert snapshots[0] == snapshots[1] and dumps[0] == dumps[1]
    assert [item.record.inspection_id for item in snapshots[0]] == sorted(
        item.record.inspection_id for item in observations
    )


def test_opaque_link_manifest_cannot_be_rewritten_under_an_unchanged_identity(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    observed = known_observation(omitted=True)
    record_senate_inspections(repo, (observed,), recorded_at=NOW)
    valid = repo.dump()
    record = repo.get_senate_inspection(observed.record.inspection_id)
    assert record is not None, "The explicit caller saved the immutable omitted fact"
    changed_link = observed.links[0].model_copy(update={"reader_text_sha256": None})
    manifest = senate_sha256([changed_link.model_dump(mode="json")])
    changed = record.model_copy(update={"links_sha256": manifest})
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        db.execute("UPDATE senate_inspection_sources SET reader_text_sha256=NULL")
        db.execute(
            "UPDATE senate_inspections SET links_sha256=?,record_json=?",
            (manifest, changed.model_dump_json()),
        )
    with pytest.raises(ValueError):
        repo.get_senate_inspection(record.inspection_id)
    corrupt = repo.dump()
    repo.close()
    assert "invalid_senate_inspection" in {
        item["code"] for item in audit(tmp_path / "history.db")["findings"]
    }
    target = SqliteBillRepository(tmp_path / "target.db")
    target.restore(valid)
    with pytest.raises(ValueError):
        target.restore(corrupt)
    assert target.dump() == valid
    target.close()


def test_history_reuse_skips_rolled_back_owner_without_loading_an_existing_aggregate(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    collector = SenateInspectionCollector()
    repo.begin()
    repo.upsert_summary(
        ProcessSummary(
            term=10,
            number="temporary",
            title="Temporary",
            document_type="projekt ustawy",
            change_date=NOW,
        ),
        now=NOW,
    )
    value = payload().model_copy(
        update={"owner": payload().owner.model_copy(update={"number": "temporary"})}
    )
    collector.observe(
        SenateInspectionObservation(SenateInspectionRecord.of(value), value.physical_links())
    )
    repo.rollback()
    assert not repo.bill_exists(10, "temporary")
    known = known_observation()
    collector.observe(known)
    with closing(sqlite3.connect(tmp_path / "history.db")) as db, db:
        db.execute("UPDATE bills SET summary_json='not JSON' WHERE number='1929'")
    assert repo.bill_exists(10, "1929")
    with pytest.raises(ValueError):
        repo.get(10, "1929")
    with repo.atomic():
        SenateInspectionPersistence(collector).persist_source_history(repo, recorded_at=NOW)
    assert repo.get_senate_inspection(known.record.inspection_id) is not None
    assert not tuple(repo.iter_senate_inspections(10, "temporary"))
    repo.close()


def test_duplicate_selection_preserves_each_actual_extraction_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repository(tmp_path)
    owner = repo.get(10, "1929")
    assert owner is not None, "The fixture explicitly enrolled its real Bill owner"
    body = docx(escape(TEXT.replace("\f", "\n")))

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        return body

    loader = TextLoader({"api.sejm.gov.pl": download}, extractor(), max_bytes=5 * 1024 * 1024)
    loaded = loader.read(URL)
    artifact, receipt, result = loader.file_origins(URL, loaded)
    assert artifact is not None and result is not None, (
        "The real Word extractor returned exact physical metadata"
    )
    alternate = result.model_copy(update={"package_policy_source_sha256": "f" * 64})
    index = 0

    def origins(
        self: TextLoader, url: str, value: LoadedFile
    ) -> tuple[ArtifactIdentity | None, ArtifactReadIdentity | None, ExtractionResult | None]:
        nonlocal index
        index += 1
        return artifact, receipt, result if index == 1 else alternate

    monkeypatch.setattr(TextLoader, "file_origins", origins)
    value = payload()
    info = PrintInfo(
        term=10,
        number="3213",
        title=value.context.print_title,
        attachments=tuple(
            Attachment(print_number="3213", name=name, url=URL)
            for name in ("first.docx", "alias.docx")
        ),
    )
    collector = SenateInspectionCollector()
    capture = SenateInspectionCapture(
        owner,
        info,
        Stage.model_validate(value.context.stage.model_dump()),
        titles=(SUBJECT,),
        passed_on=None,
        resolver_source=__file__,
        observe=collector.observe,
    )
    evidence = inspect_senate_resolution(loaded.text or "", word_eof=True)
    assert evidence.complete and evidence.adopted_on is not None, (
        "The real Word fixture contains a full Senate resolution"
    )
    for descriptor_index in (0, 1):
        capture.start(descriptor_index, "word")
        capture.read(loader, URL, loaded)
        capture.inspected(
            evidence, word_eof=True, reason="", normalized=" ".join(evidence.text.split())
        )
    capture.phase("word", incomplete=False)
    capture.result("matching_equivalent_word", 0)
    capture.finish(
        TextDocument.model_validate(
            {
                "url": URL,
                "kind": "senate_amendments",
                "senate": {
                    "sha256": artifact.sha256,
                    "subject": evidence.subject,
                    "adopted_on": evidence.adopted_on,
                    "word_eof": True,
                },
            }
        ),
        None,
    )
    snapshots = collector.snapshot()
    assert len(snapshots) == 1 and len(snapshots[0].sources) == 1
    assert {link.extraction_id for link in snapshots[0].links} == {
        result.result_id,
        alternate.result_id,
    }
    assert len(snapshots[0].sources[0].extractions) == 2
    record_senate_inspections(repo, snapshots, recorded_at=NOW)
    assert repo.get_senate_inspection(snapshots[0].record.inspection_id) is not None
    repo.restore(repo.dump())
    repo.close()


@pytest.mark.parametrize("omitted", [False, True])
@pytest.mark.parametrize(
    "suffix", ["", "?token=SECRET_SENATE_CREDENTIAL", "#SECRET_SENATE_CREDENTIAL", "userinfo"]
)
def test_public_capture_never_persists_unsafe_urls_and_keeps_url_free_body_parents(
    tmp_path: Path,
    suffix: str,
    omitted: bool,
) -> None:
    repo = repository(tmp_path)
    owner = repo.get(10, "1929")
    assert owner is not None, "The public producer receives the enrolled Bill owner"
    url = (
        URL.replace("https://", "https://user:SECRET_SENATE_CREDENTIAL@")
        if suffix == "userinfo"
        else URL + suffix
    )
    safe = suffix == ""
    body = docx(escape(TEXT.replace("\f", "\n")))
    calls: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        calls.append(url)
        return body

    loader = TextLoader({"api.sejm.gov.pl": download}, extractor(), max_bytes=5 * 1024 * 1024)
    collector = SenateInspectionCollector()
    analysis = AnalysisService(
        repo,
        SejmTextSource(FakeSejmGateway()),
        loader,
        FakeLlm(),
        FixedClock(NOW),
        AnalysisOptions(),
        text_budget=TextBudget(60000),
        senate_inspections=collector,
    )
    template = payload()
    stage = Stage.model_validate(template.context.stage.model_dump())
    info = PrintInfo(
        term=10,
        number="3213",
        title=template.context.print_title,
        attachments=(
            Attachment(
                print_number="3213",
                name=("ą" * 6000 if omitted else "resolution") + ".docx",
                url=url,
            ),
        ),
    )
    selected = analysis.senate_amendments_document(info, stage, titles=(SUBJECT,), owner=owner)
    assert selected is not None and selected.url == url and selected.senate is not None
    assert selected.senate.sha256 == hashlib.sha256(body).hexdigest() and calls == [url]
    snapshots = collector.snapshot()
    assert len(snapshots) == 1
    link = snapshots[0].links[0]
    assert link.artifact_sha256 == selected.senate.sha256 and link.artifact_bytes == len(body)
    assert link.extraction_id is not None
    assert (link.artifact_read_id is not None) is safe
    assert (link.selection_id is not None) is safe
    assert (link.observation_id is not None) is safe
    assert len(snapshots[0].sources) == int(safe)
    assert len(snapshots[0].extractions) == int(not safe)
    with repo.atomic():
        SenateInspectionPersistence(collector).persist_source_history(repo, recorded_at=NOW)
    saved = repo.get_senate_inspection(snapshots[0].record.inspection_id)
    assert saved is not None and (saved.coverage == "omitted_over_limit") is omitted
    script = repo.dump()
    assert "SECRET_SENATE_CREDENTIAL" not in script
    assert repo.get_extraction_result(link.extraction_id) is not None
    assert (not repo.list_source_observations(10, "1929")) is (not safe)
    repo.restore(script)
    assert repo.dump() == script
    repeated = analysis.senate_amendments_document(info, stage, titles=(SUBJECT,), owner=owner)
    assert repeated == selected and calls == [url]
    with repo.atomic():
        SenateInspectionPersistence(collector).persist_source_history(
            repo, recorded_at=NOW + dt.timedelta(days=365)
        )
    assert repo.dump() == script
    repo.close()
    assert not audit(tmp_path / "history.db")["findings"]


@pytest.mark.parametrize("omitted", [False, True])
def test_nonoutage_abort_history_roundtrips_without_claiming_source_outage(
    tmp_path: Path, *, omitted: bool
) -> None:
    data = payload(name=("ą" * 6000 + ".docx") if omitted else "3213-uchwala.docx").model_dump(
        mode="json"
    )
    first = SenateInspectionCandidate.model_validate(data["universe"][0])
    with pytest.raises(ValueError, match="skip or failed read"):
        SenateInspectionCandidate.model_validate(
            {
                **first.model_dump(),
                "inspection": {**first.inspection.model_dump(), "reason": "after_failure"},
            }
        )
    active = SenateInspectionCandidate.model_validate(
        {
            "descriptor": first.descriptor.model_dump(),
            "inspection": {
                "phase": "word",
                "attempted": True,
                "state": "read_error",
                "reason": "read_error",
                "error_class": "KeyboardInterrupt",
            },
        }
    )
    remaining = SenateInspectionCandidate.model_validate(
        {
            "descriptor": {
                **first.descriptor.model_dump(),
                "index": 1,
                "name": "other.pdf",
            },
            "inspection": {
                "phase": "filter",
                "attempted": False,
                "state": "unattempted",
                "reason": "after_failure",
            },
        }
    )
    data.update(
        universe=[active.model_dump(), remaining.model_dump()],
        phases=[],
        result={
            "outcome": "propagated_failure",
            "descriptor_index": None,
            "identity": None,
            "error_class": "KeyboardInterrupt",
        },
    )
    value = SenateInspectionPayload.model_validate(data)
    record = SenateInspectionRecord.of(value)
    assert isinstance(record.payload, SenateInspectionOmission) == omitted
    observation = SenateInspectionObservation(record, value.physical_links())
    repo = repository(tmp_path)
    record_senate_inspections(repo, (observation,), recorded_at=NOW)
    saved = repo.get_senate_inspection(record.inspection_id)
    assert saved is not None, "The caller committed the exact abort observation"
    assert saved.payload == record.payload
    before = repo.dump()
    record_senate_inspections(repo, (observation,), recorded_at=NOW + dt.timedelta(days=365))
    assert repo.dump() == before
    restored = SqliteBillRepository(tmp_path / "restored.db")
    restored.restore(before)
    assert restored.dump() == before
    assert restored.get_senate_inspection(record.inspection_id) == saved
    restored.close()
    repo.close()
    assert audit(tmp_path / "history.db")["findings"] == []
