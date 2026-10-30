import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from lexinform.models.candidate_trace import MAX_CANDIDATE_TRACE_BYTES
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.scenario.test_document_selection_contract import law

NOW = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)
HOST = "https://api.test/"


def loader(
    files: dict[str, bytes], texts: dict[bytes, str] | None = None
) -> tuple[TextLoader, FakeSejmGateway]:
    gateway = FakeSejmGateway(files=files)
    return (
        TextLoader(
            {"api.test": gateway.download},
            FakeTextExtractor(law(), page_count=0, by_content=texts),
            max_bytes=1000,
        ),
        gateway,
    )


def test_routine_singleton_uses_source_observation_without_extra_trace() -> None:
    url = HOST + "selected"
    requested = TextDocument(url=url, kind="rcl")
    disposition = CandidateDisposition(
        phase="fallback",
        index=0,
        outcome="selected",
        reason="first_acceptable_candidate",
        observed_sha256=hashlib.sha256(b"selected").hexdigest(),
    )
    trace = CandidateDispositionTrace.from_decisions(
        requested,
        (disposition,),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="unverified",
    )
    reader, gateway = loader({url: b"selected"})

    selected, loaded = reader.select_document(requested)

    assert trace.is_routine_singleton
    assert selected == requested and loaded.text == law()
    assert loaded.candidate_trace is None
    assert gateway.calls == ["download:" + url]


def test_version_trace_records_every_probe_and_policy_choice_without_body() -> None:
    conflict = TextDocument(url=HOST + "conflict", kind="rcl", official_link_conflict=True)
    missing = TextDocument(url=HOST + "missing", kind="rcl")
    note = TextDocument(url=HOST + "note", kind="rcl")
    old = TextDocument(url=HOST + "old", kind="rcl")
    current = TextDocument(url=HOST + "current", kind="rcl")
    requested = conflict.model_copy(
        update={"compare_versions": True, "alternatives": (missing, note, old, current)}
    )
    reader, gateway = loader(
        {note.url: b"note", old.url: b"old", current.url: b"current"},
        {
            b"note": "UZASADNIENIE\n" + "To nie jest projekt ustawy. " * 20,
            b"old": law(day=1),
            b"current": law(day=2),
        },
    )

    selected, loaded = reader.select_document(requested)

    assert selected == current and loaded.text == law(day=2)
    trace = loaded.candidate_trace
    assert trace is not None and trace.coverage == "complete"
    assert trace.requested_document == requested and trace.result == "selected"
    assert [(item.phase, item.index, item.outcome, item.reason) for item in trace.dispositions] == [
        ("version_comparison", 0, "official_conflict", "official_link_conflict"),
        ("version_comparison", 1, "read_failure", "read_error"),
        ("version_comparison", 2, "non_bill", "non_bill_body"),
        ("version_comparison", 3, "policy_excluded", "version_preference"),
        ("version_comparison", 4, "selected", "version_choice"),
    ]
    assert [item.observed_sha256 for item in trace.dispositions] == [
        None,
        None,
        hashlib.sha256(b"note").hexdigest(),
        hashlib.sha256(b"old").hexdigest(),
        hashlib.sha256(b"current").hexdigest(),
    ]
    assert not any("conflict" in call for call in gateway.calls)
    assert law(day=2) not in trace.model_dump_json(exclude_defaults=True)
    assert len(trace.model_dump_json(exclude_defaults=True).encode()) <= MAX_CANDIDATE_TRACE_BYTES
    assert (
        CandidateDispositionTrace.model_validate_json(trace.model_dump_json(exclude_defaults=True))
        == trace
    )


def test_fallback_trace_marks_unattempted_tail_without_changing_read_order() -> None:
    conflict = TextDocument(url=HOST + "conflict", kind="rcl", official_link_conflict=True)
    missing = TextDocument(url=HOST + "missing", kind="rcl")
    appendix = TextDocument(url=HOST + "appendix", kind="rcl")
    selected = TextDocument(url=HOST + "selected", kind="rcl")
    untouched = TextDocument(url=HOST + "untouched", kind="rcl")
    requested = conflict.model_copy(
        update={"alternatives": (missing, appendix, selected, untouched)}
    )
    reader, gateway = loader(
        {appendix.url: b"appendix", selected.url: b"selected", untouched.url: b"untouched"},
        {b"appendix": "ROZPORZĄDZENIE\n" + "Projekt rozporządzenia. " * 20},
    )

    chosen, loaded = reader.select_document(requested)

    assert chosen == selected and loaded.text == law()
    trace = loaded.candidate_trace
    assert trace is not None and trace.requested_document == requested
    assert [(item.index, item.outcome) for item in trace.dispositions] == [
        (0, "official_conflict"),
        (1, "read_failure"),
        (2, "policy_excluded"),
        (3, "selected"),
        (4, "unattempted"),
    ]
    assert trace.dispositions[2].reason == "appendix_kind"
    assert trace.dispositions[-1].reason == "selection_stopped"
    assert [item.observed_sha256 for item in trace.dispositions] == [
        None,
        None,
        hashlib.sha256(b"appendix").hexdigest(),
        hashlib.sha256(b"selected").hexdigest(),
        None,
    ]
    assert gateway.calls == [
        "download:" + missing.url,
        "download:" + appendix.url,
        "download:" + selected.url,
    ]


def test_chosen_outer_candidate_can_still_have_waiting_bundle() -> None:
    main = HOST + "main"
    required = HOST + "required"
    requested = TextDocument(url=main, kind="rcl", required_urls=(required,))
    reader, _ = loader({main: b"main", required: b"required"}, {b"required": ""})

    selected, loaded = reader.select_document(requested)

    assert selected == requested and loaded.selection.state == "waiting"
    trace = loaded.candidate_trace
    assert trace is not None and trace.result == "selected"
    assert trace.selection_state == "waiting"
    assert trace.dispositions[0].reason == "first_acceptable_candidate"


def test_textless_version_probe_is_unclassified_before_scan_fallback() -> None:
    url = HOST + "scan"
    requested = TextDocument(url=url, kind="rcl", compare_versions=True)
    gateway = FakeSejmGateway(files={url: b"scan"})
    reader = TextLoader(
        {"api.test": gateway.download}, FakeTextExtractor("", page_count=5), max_bytes=1000
    )

    selected, loaded = reader.select_document(requested)

    assert selected == requested and loaded.is_scan and loaded.pages == 5
    trace = loaded.candidate_trace
    assert trace is not None and trace.result == "selected"
    assert [(item.phase, item.outcome, item.reason) for item in trace.dispositions] == [
        ("version_comparison", "unclassified", "scan_without_text"),
        ("fallback", "selected", "first_acceptable_candidate"),
    ]
    assert [item.observed_sha256 for item in trace.dispositions] == [
        hashlib.sha256(b"scan").hexdigest()
    ] * 2
    assert gateway.calls == ["download:" + url]


def test_overflow_is_an_explicit_digest_and_count_without_partial_candidate_list() -> None:
    alternatives = tuple(
        TextDocument(
            url=HOST + f"long-candidate-{index}-" + "x" * 80,
            kind="rcl",
            official_link_conflict=True,
        )
        for index in range(60)
    )
    requested = TextDocument(
        url=HOST + "conflict",
        kind="rcl",
        official_link_conflict=True,
        alternatives=alternatives,
    )
    reader, gateway = loader({})

    _, loaded = reader.select_document(requested)

    trace = loaded.candidate_trace
    assert trace is not None and trace.coverage == "omitted_over_limit"
    assert trace.requested_document is None and not trace.dispositions
    assert trace.candidate_count == 61 and trace.omitted_dispositions == 61
    universe = json.dumps(
        requested.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    assert trace.universe_sha256 == hashlib.sha256(universe.encode()).hexdigest()
    assert len(trace.model_dump_json(exclude_defaults=True).encode()) <= MAX_CANDIDATE_TRACE_BYTES
    assert not gateway.calls


def test_overlarge_request_keeps_selected_file_and_records_explicit_omission() -> None:
    chosen = TextDocument(url=HOST + "selected", kind="rcl")
    requested = TextDocument(
        url=HOST + "conflict-" + "x" * 5000,
        kind="rcl",
        official_link_conflict=True,
        alternatives=(chosen,),
    )
    reader, gateway = loader({chosen.url: b"selected"})

    selected, loaded = reader.select_document(requested)

    assert selected == chosen and loaded.text == law()
    assert loaded.candidate_trace is None
    capture = reader.capture_source_read(requested, selected, loaded)
    assert capture.artifacts and capture.reads
    diagnostic = reader.read_metadata_url_omissions()
    assert diagnostic is not None and diagnostic.total == 1
    assert diagnostic.reasons[0].reason == "oversized_url"
    assert gateway.calls == ["download:" + chosen.url]


def test_trace_serialization_failure_does_not_change_document_choice(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    url = HOST + "selected"
    reader, gateway = loader({url: b"selected"})

    def fail(*args: object, **kwargs: object) -> CandidateDispositionTrace:
        raise RuntimeError("trace-only error")

    monkeypatch.setattr(CandidateDispositionTrace, "from_decisions", fail)

    selected, loaded = reader.select_document(TextDocument(url=url, kind="rcl"))

    assert selected.url == url and loaded.text == law()
    assert loaded.candidate_trace is None
    assert "candidate disposition trace omitted" in caplog.text
    assert gateway.calls == ["download:" + url]


def test_same_source_observation_links_distinct_rejected_universes_and_repeats(
    tmp_path: Path,
) -> None:
    chosen = TextDocument(url=HOST + "selected", kind="rcl")
    requested = [
        TextDocument(
            url=HOST + f"conflict-{index}",
            kind="rcl",
            official_link_conflict=True,
            alternatives=(chosen,),
        )
        for index in range(2)
    ]
    reader, gateway = loader({chosen.url: b"selected"})
    repo = SqliteBillRepository(tmp_path / "state.db")
    repo.migrate()
    traces = []
    for document in requested:
        selected, loaded = reader.select_document(document)
        assert selected == chosen and loaded.candidate_trace is not None
        traces.append(loaded.candidate_trace)
        record_source_read(
            repo,
            DocumentSourceRead(
                selected,
                DocumentSelectionRecord.from_selection(loaded.selection),
                reader.artifacts_for_selection(loaded.selection),
                candidate_trace=loaded.candidate_trace,
            ),
            term=10,
            number="4100",
            purpose="analysis",
            recorded_at=NOW,
        )
    observed = repo.list_source_observations(10, "4100")
    assert len(observed) == 1
    assert {
        trace.trace_id
        for trace in repo.list_candidate_disposition_traces(observed[0].observation_id)
    } == {trace.trace_id for trace in traces}
    first_dump = repo.dump()
    record_source_read(
        repo,
        DocumentSourceRead(
            chosen,
            DocumentSelectionRecord.from_selection(loaded.selection),
            reader.artifacts_for_selection(loaded.selection),
            candidate_trace=traces[0],
        ),
        term=10,
        number="4100",
        purpose="analysis",
        recorded_at=NOW + dt.timedelta(days=10),
    )
    assert repo.dump() == first_dump
    repo.restore(first_dump)
    assert len(repo.list_candidate_disposition_traces(observed[0].observation_id)) == 2
    assert gateway.calls == ["download:" + chosen.url]
    record_source_read(
        repo,
        DocumentSourceRead(
            chosen,
            DocumentSelectionRecord.from_selection(loaded.selection),
            reader.artifacts_for_selection(loaded.selection),
            candidate_trace=traces[0],
        ),
        term=10,
        number="4101",
        purpose="analysis",
        recorded_at=NOW,
    )
    other = repo.list_source_observations(10, "4101")
    assert len(other) == 1
    assert [
        trace.trace_id for trace in repo.list_candidate_disposition_traces(other[0].observation_id)
    ] == [traces[0].trace_id]
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT count(*) FROM candidate_disposition_traces").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM source_candidate_traces").fetchone()[0] == 3


def test_trace_link_rolls_back_with_calling_transaction(tmp_path: Path) -> None:
    requested = TextDocument(url=HOST + "conflict", kind="rcl", official_link_conflict=True)
    reader, _ = loader({})
    selected, loaded = reader.select_document(requested)
    assert loaded.candidate_trace is not None
    repo = SqliteBillRepository(tmp_path / "state.db")
    repo.migrate()
    before = repo.dump()

    with pytest.raises(RuntimeError, match="checkpoint"), repo.atomic():
        record_source_read(
            repo,
            DocumentSourceRead(
                selected,
                DocumentSelectionRecord.from_selection(loaded.selection),
                (),
                candidate_trace=loaded.candidate_trace,
            ),
            term=10,
            number="4100",
            purpose="analysis",
            recorded_at=NOW,
        )
        raise RuntimeError("checkpoint failed")

    assert repo.dump() == before


def test_oversized_complete_trace_is_rejected_on_read() -> None:
    requested = TextDocument(url=HOST + "conflict", kind="rcl", official_link_conflict=True)
    reader, _ = loader({})
    _, loaded = reader.select_document(requested)
    trace = loaded.candidate_trace
    assert trace is not None and trace.coverage == "complete"
    invalid = trace.model_dump(mode="json")
    invalid["requested_document"]["url"] = "x" * 5000
    requested_document = TextDocument.model_validate(invalid["requested_document"])
    universe = json.dumps(
        requested_document.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    invalid["universe_sha256"] = hashlib.sha256(universe.encode()).hexdigest()

    with pytest.raises(ValidationError, match="metadata limit"):
        CandidateDispositionTrace.model_validate(invalid)


def test_schema_58_dump_restores_without_inferred_candidate_history(tmp_path: Path) -> None:
    previous = sqlite3.connect(tmp_path / "previous.db")
    for migration in MIGRATIONS[:58]:
        previous.executescript(migration)
    selection = DocumentSelectionRecord.from_selection(DocumentSelection())
    source = SourceObservation(
        term=10,
        number="4100",
        purpose="analysis",
        document=TextDocument(url=HOST + "selected", kind="rcl"),
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    previous.execute(
        "INSERT INTO document_selections"
        " (selection_id, question, policy_version, state, record_json) VALUES (?, ?, ?, ?, ?)",
        (
            selection.selection_id,
            selection.selection.question,
            selection.selection.policy_version,
            selection.selection.state,
            selection.model_dump_json(),
        ),
    )
    previous.execute(
        "INSERT INTO source_observations"
        " (observation_id, term, number, purpose, selection_id, source_url, source_kind,"
        " observation_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source.observation_id,
            source.term,
            source.number,
            source.purpose,
            source.selection_id,
            source.document.url,
            source.document.kind,
            source.model_dump_json(),
        ),
    )
    old_dump = "\n".join(previous.iterdump()) + "\nPRAGMA user_version = 58;\n"
    previous.close()
    repo = SqliteBillRepository(tmp_path / "current.db")
    repo.migrate()

    repo.restore(old_dump)

    assert repo.schema_version == SCHEMA_VERSION
    assert repo.get_source_observation(source.observation_id) == source
    assert not repo.list_candidate_disposition_traces(source.observation_id)


@pytest.mark.parametrize("corruption", ["checksum", "orphan_link"])
def test_restore_rejects_corrupt_trace_before_replacing_database(
    tmp_path: Path, corruption: str
) -> None:
    requested = TextDocument(url=HOST + "conflict", kind="rcl", official_link_conflict=True)
    reader, _ = loader({})
    selected, loaded = reader.select_document(requested)
    assert loaded.candidate_trace is not None
    repo = SqliteBillRepository(tmp_path / "state.db")
    repo.migrate()
    record_source_read(
        repo,
        DocumentSourceRead(
            selected,
            DocumentSelectionRecord.from_selection(loaded.selection),
            (),
            candidate_trace=loaded.candidate_trace,
        ),
        term=10,
        number="4100",
        purpose="analysis",
        recorded_at=NOW,
    )
    original = repo.dump()
    replacement_id = "candidate-trace-v1:" + "f" * 64
    if corruption == "checksum":
        mutation = (
            "UPDATE candidate_disposition_traces SET trace_id='" + replacement_id + "';\n"
            "UPDATE source_candidate_traces SET trace_id='" + replacement_id + "';\n"
        )
    else:
        mutation = "UPDATE source_candidate_traces SET trace_id='" + replacement_id + "';\n"
    marker = f"PRAGMA user_version = {SCHEMA_VERSION};"
    corrupt = original.replace(marker, mutation + marker)

    with pytest.raises(ValueError, match="candidate trace"):
        repo.restore(corrupt)

    assert repo.dump() == original
