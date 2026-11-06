import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from lexinform.adapters.document_work import DocumentWorkRowError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import DocumentWork, ProcessDetail, Stage, StatusChange
from lexinform.models.documents import SenateAmendmentInputWitness
from lexinform.models.input_receipts import amendment_context_sha256, input_text_sha256
from tests.unit.test_input_receipts import NOW
from tests.unit.test_senate_input_capture import URL, senate_case


def witnessed_work(
    repo: SqliteBillRepository, process: ProcessDetail, *, prefix: bool = False
) -> DocumentWork:
    case, _, calls, full_text = senate_case(repo, process, prefix=prefix)
    context = case.service.amendments_context(case.bill, case.document)
    assert context is not None, "the fixture provides a verified resolution"
    assert calls == [URL]
    offset = len(full_text) - len(context.text)
    assert full_text[offset:] == context.text and (offset > 0) == prefix
    (observation,) = repo.list_source_observations(case.bill.term, case.bill.number)
    (extraction,) = repo.list_selection_extractions(observation.selection_id)
    stage = Stage(stage_type="Senate", stage_name="Senat")
    change_id = repo.add_status_change(
        StatusChange(
            term=case.bill.term,
            number=case.bill.number,
            old_fingerprint=None,
            new_fingerprint="senate",
            new_stages=[stage],
            detected_at=NOW,
        )
    )
    assert change_id is not None, "the fixture records an actual historical event"
    return DocumentWork(
        key=f"amendments/{change_id}",
        term=case.bill.term,
        number=case.bill.number,
        change_id=change_id,
        bill_json=case.bill.model_dump_json(),
        detail_json=process.model_dump_json(),
        stage_json=stage.model_dump_json(),
        created_at=NOW,
        next_at=NOW,
        document_json=case.document.model_dump_json(),
        context_json=context.model_dump_json(),
        senate_input_witness=SenateAmendmentInputWitness(
            source_observation_id=observation.observation_id,
            extraction_id=extraction.extraction_id,
            context_sha256=amendment_context_sha256(context),
            text_sha256=input_text_sha256(context.text),
            source_start=offset,
        ),
    )


@pytest.mark.parametrize("prefix", [False, True])
def test_senate_witness_preserves_exact_restore_and_scheduled_read(
    repo: SqliteBillRepository, process_3039: ProcessDetail, prefix: bool
) -> None:
    work = witnessed_work(repo, process_3039, prefix=prefix)
    legacy = work.model_copy(update={"senate_input_witness": None})
    assert '"senate_input_witness"' not in legacy.model_dump_json()
    repo.save_document_work(legacy)
    old_dump = repo.dump()
    repo.restore(old_dump)
    assert repo.dump() == old_dump
    assert repo.list_document_work(due=NOW) == [legacy]

    repo.save_document_work(work)
    retained = repo.dump()
    for _ in range(2):
        repo.restore(retained)
        assert repo.dump() == retained
        assert repo.list_document_work(due=NOW) == [work]

    later = NOW + timedelta(days=1)
    delayed = work.model_copy(update={"next_at": later, "attempts": 1})
    repo.save_document_work(delayed)
    assert repo.list_document_work(due=NOW) == []
    assert repo.list_document_work(due=later) == [delayed]
    with pytest.raises(ValueError, match="cannot replace its frozen input witness"):
        repo.save_document_work(legacy)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "source_observation_id",
            "source-observation-v1:" + "f" * 64,
            "invalid_senate_input_witness",
        ),
        ("extraction_id", "extraction-v1:" + "f" * 64, "senate_input_extraction_missing"),
        ("context_sha256", "f" * 64, "invalid_senate_input_witness"),
        ("text_sha256", "f" * 64, "invalid_senate_input_witness"),
        ("source_start", 1, "invalid_senate_input_witness"),
    ],
)
def test_senate_witness_rejects_bad_parent_or_projection_on_save_and_restore(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    field: str,
    value: object,
    code: str,
) -> None:
    work = witnessed_work(repo, process_3039)
    witness = work.senate_input_witness
    assert witness is not None, "the fixture binds the actual source observation and extraction"
    corrupt = work.model_copy(
        update={"senate_input_witness": witness.model_copy(update={field: value})}
    )
    before = repo.dump()
    with pytest.raises(DocumentWorkRowError, match=code):
        repo.save_document_work(corrupt)
    assert repo.dump() == before
    repo.save_document_work(work)
    valid = repo.dump()
    with closing(sqlite3.connect(":memory:")) as sql:
        sql.executescript(valid)
        sql.execute("UPDATE document_work SET work_json=?", (corrupt.model_dump_json(),))
        damaged = "\n".join(sql.iterdump()) + f"\nPRAGMA user_version = {repo.schema_version};\n"
    with pytest.raises(DocumentWorkRowError, match=code):
        repo.restore(damaged)
    assert repo.dump() == valid


@pytest.mark.parametrize("foreign_fields", [{"term": 9}, {"number": "3040"}, {"change_id": 2}])
def test_senate_witness_cannot_overwrite_another_work_owner(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    foreign_fields: dict[str, int | str],
) -> None:
    work = witnessed_work(repo, process_3039)
    conflicting = work.model_copy(update={**foreign_fields, "senate_input_witness": None})
    repo.save_document_work(conflicting)
    before = repo.dump()

    with pytest.raises(ValueError, match="cannot replace its frozen input witness"):
        repo.save_document_work(work)

    assert repo.dump() == before
    assert repo.list_document_work(due=NOW) == [conflicting]


def test_senate_witness_requires_canonical_key_and_actual_change_owner(
    repo: SqliteBillRepository, process_3039: ProcessDetail
) -> None:
    work = witnessed_work(repo, process_3039)
    foreign_change = repo.add_status_change(
        StatusChange(
            term=work.term,
            number="3040",
            old_fingerprint=None,
            new_fingerprint="senate",
            new_stages=[],
            detected_at=NOW,
        )
    )
    assert foreign_change is not None, "the fixture records another bill's event"
    before = repo.dump()
    for invalid in (
        work.model_copy(update={"key": "other"}),
        work.model_copy(
            update={"key": f"amendments/{foreign_change}", "change_id": foreign_change}
        ),
    ):
        with pytest.raises(DocumentWorkRowError, match="invalid_senate_input_witness"):
            repo.save_document_work(invalid)
        assert repo.dump() == before
