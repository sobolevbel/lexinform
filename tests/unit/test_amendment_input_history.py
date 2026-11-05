import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.computation_doctor import audit
from tools.replay_content_applications import poster, queued, revision

from lexinform.adapters.amendment_inputs import amendment_input_issues
from lexinform.adapters.sqlite_repo import MIGRATIONS, SqliteBillRepository
from lexinform.json_storage import canonical_json
from lexinform.models import (
    AmendmentInputRef,
    AmendmentInputSnapshot,
    AmendmentsContext,
    AmendmentsQuestion,
    AmendmentsRecord,
    BatchIntent,
    BatchRequest,
    ComputationInputBinding,
    ComputationResult,
    DigestItemMeta,
    LlmBatch,
    LlmBatchItem,
)
from lexinform.models.input_receipts import AmendmentInputReceipt, amendment_context_sha256
from lexinform.models.prepared_input import PreparedTextPart
from lexinform.services.amendment_input_history import original_amendment_input
from tests.computations import NOW, retained_result
from tests.fakes import FakePublisher
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case


def test_reader_migration_admits_dedicated_input_table_without_rows() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        with closing(sqlite3.connect(":memory:")) as copy:
            copy.executescript(repo.dump())
            found = copy.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='amendment_input_snapshots'"
            ).fetchone()
            assert found is not None, "the reader migration must add its dedicated immutable table"
            assert copy.execute("SELECT count(*) FROM amendment_input_snapshots").fetchone() == (0,)


def input_snapshot(text: str = "Art. 1. Zachowano pierwotny tekst.") -> AmendmentInputSnapshot:
    context = AmendmentsContext(
        number="3039",
        title="Original title",
        source_kind="committee_amendments",
        text=text,
        truncated=False,
        previous_summary="Original comparison",
        previous_key_changes=["Original change"],
        proposal="Actual proposal",
        recommendation="Actual recommendation",
    )
    return AmendmentInputSnapshot(
        question_json=canonical_json(AmendmentsQuestion(ctx=context).model_dump(mode="json"))
    )


def reference(snapshot: AmendmentInputSnapshot, request_sha256: str) -> AmendmentInputRef:
    return AmendmentInputRef(
        snapshot_id=snapshot.snapshot_id,
        question_sha256=snapshot.question_sha256,
        request_sha256=request_sha256,
    )


def share_snapshot_text(path: Path, snapshot: AmendmentInputSnapshot) -> None:
    context = snapshot.context.model_dump(mode="json", exclude_unset=True)
    text = snapshot.context.text
    del context["text"]
    digest = hashlib.sha256(text.encode()).hexdigest()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("INSERT INTO amendment_input_texts VALUES (?,?)", (digest, text))
        connection.execute(
            "UPDATE amendment_input_snapshots SET question_json=?,text_sha256=? WHERE snapshot_id=?",
            (canonical_json({"kind": "amendments", "ctx": context}), digest, snapshot.snapshot_id),
        )


def test_restore_of_v67_inline_question_preserves_original_bytes() -> None:
    value = input_snapshot("Art. 1. Zmiana: 200 zł.\n")
    with closing(sqlite3.connect(":memory:")) as legacy:
        legacy.executescript("\n".join(MIGRATIONS[:67]))
        legacy.execute(
            "INSERT INTO amendment_input_snapshots VALUES (?,?,?,?)",
            (value.snapshot_id, value.profile, value.question_sha256, value.question_json),
        )
        legacy.commit()
        dump = "\n".join(legacy.iterdump()) + "\nPRAGMA user_version = 67;\n"
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.restore(dump)
        assert repo.get_amendment_input(value.snapshot_id) == value
        migrated = repo.dump()
        repo.restore(migrated)
        assert repo.dump() == migrated


def test_shared_text_preserves_snapshot_and_paid_reference_through_restore(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    shared = input_snapshot("Art. 1. Kwota wynosi 200 zł.\n\n")
    inline = input_snapshot()
    result = paid_input(shared)
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_amendment_input(shared)
        repo.save_amendment_input(inline)
        repo.save_computation_result(result)
        share_snapshot_text(path, shared)
        assert repo.get_amendment_input(shared.snapshot_id) == shared
        assert repo.get_amendment_input(inline.snapshot_id) == inline
        assert repo.get_computation_result(result.result_id) == result
        before = repo.dump()
        repo.save_amendment_input(shared)
        repo.restore(before)
        assert repo.dump() == before
        assert repo.get_amendment_input(shared.snapshot_id) == shared


@pytest.mark.parametrize("damage", ["missing", "text", "inline_manifest"])
def test_shared_text_corruption_fails_reads_doctor_and_restore(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "state.db"
    value = input_snapshot()
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_amendment_input(value)
        share_snapshot_text(path, value)
        with closing(sqlite3.connect(path)) as connection, connection:
            if damage == "missing":
                connection.execute("DELETE FROM amendment_input_texts")
            elif damage == "text":
                connection.execute("UPDATE amendment_input_texts SET text='changed'")
            else:
                connection.execute(
                    "UPDATE amendment_input_snapshots SET question_json=?", (value.question_json,)
                )
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.get_amendment_input(value.snapshot_id)
        with closing(SqliteBillRepository(":memory:")) as target:
            target.migrate()
            before = target.dump()
            with pytest.raises(ValueError, match="invalid_amendment_input"):
                target.restore(repo.dump())
            assert target.dump() == before
    assert any(item["code"] == "invalid_amendment_input" for item in audit(path)["findings"])


def paid_input(snapshot: AmendmentInputSnapshot, *, bound: bool = True) -> ComputationResult:
    original = retained_result("amendments")
    assert original.request_sha256 is not None, (
        "the paid technical fixture retains a known frozen SHA"
    )
    assert isinstance(original.record, AmendmentsRecord), "the technical paid result is amendments"
    return ComputationResult.model_validate(
        {
            **original.model_dump(),
            "record": original.record.model_copy(
                update={"source_url": "https://offline.invalid/amendments"}
            ),
            "input_binding": ComputationInputBinding(
                term=10, number="3039", reuse_key="amendments"
            ),
            "amendment_input_ref": reference(snapshot, original.request_sha256) if bound else None,
        }
    )


def test_snapshot_is_typed_canonical_deduplicated_and_survives_restore() -> None:
    value = input_snapshot()
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_amendment_input(value)
        before = repo.dump()
        repo.save_amendment_input(value)
        assert repo.dump() == before
        assert repo.get_amendment_input(value.snapshot_id) == value
        assert value.context.previous_summary == "Original comparison"
        assert value.context.proposal == "Actual proposal"
        assert value.context.recommendation == "Actual recommendation"
        repo.restore(before)
        repo.restore(before)
        assert repo.dump() == before


def test_reader_adds_no_128k_or_production_question_size_admission_cap() -> None:
    value = input_snapshot("ą\n" * 200000)
    assert len(value.question_json.encode()) > 131072
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_amendment_input(value)
        assert repo.get_amendment_input(value.snapshot_id) == value


@pytest.mark.parametrize(
    "damage", ["unknown", "duplicate", "deep", "noncanonical", "wrong_kind", "scan"]
)
def test_snapshot_rejects_unknown_or_lossy_question_profiles(damage: str) -> None:
    raw = input_snapshot().question_json
    value = json.loads(raw)
    if damage == "unknown":
        value["ctx"]["future_body"] = "unrepresented"
    elif damage == "wrong_kind":
        value["kind"] = "supplement"
    elif damage == "scan":
        value["ctx"]["scan"] = {"raw": "not a retained image profile"}
    raw = canonical_json(value)
    if damage == "duplicate":
        raw = raw[:-1] + ',"kind":"amendments"}'
    elif damage == "deep":
        raw = '{"kind":"amendments","ctx":' + "[" * 2000 + "0" + "]" * 2000 + "}"
    elif damage == "noncanonical":
        raw += " "
    with pytest.raises(ValueError):
        AmendmentInputSnapshot(question_json=raw)


def test_first_result_ref_is_checked_on_save_get_list_and_restore() -> None:
    value = input_snapshot()
    result = paid_input(value)
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.save_computation_result(result)
        repo.save_amendment_input(value)
        repo.save_computation_result(result)
        assert repo.get_computation_result(result.result_id) == result
        assert repo.list_computation_results(10, "3039", purpose="amendments") == [result]
        before = repo.dump()
        repo.restore(before)
        repo.restore(before)
        assert repo.dump() == before


@pytest.mark.parametrize("damage", ["sha", "body", "blob", "missing", "result_ref"])
def test_broken_known_inputs_fail_public_reads_doctor_and_atomic_restore(
    tmp_path: Path, damage: str
) -> None:
    path = tmp_path / "state.db"
    value = input_snapshot()
    result = paid_input(value)
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_amendment_input(value)
        repo.save_computation_result(result)
        with closing(sqlite3.connect(path)) as connection:
            if damage == "missing":
                connection.execute("DELETE FROM amendment_input_snapshots")
            elif damage == "result_ref":
                raw = result.model_dump(mode="json")
                raw["amendment_input_ref"]["question_sha256"] = "0" * 64
                connection.execute(
                    "UPDATE computation_results SET result_json=?", (json.dumps(raw),)
                )
            elif damage == "sha":
                connection.execute(
                    "UPDATE amendment_input_snapshots SET question_sha256=?", ("0" * 64,)
                )
            elif damage == "body":
                connection.execute(
                    "UPDATE amendment_input_snapshots SET question_json=?",
                    (value.question_json.replace("Original comparison", "Changed comparison"),),
                )
            else:
                connection.execute(
                    "UPDATE amendment_input_snapshots SET question_json=?",
                    (value.question_json.encode(),),
                )
            connection.commit()
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.get_computation_result(result.result_id)
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.list_computation_results(10, "3039")
        script = repo.dump()
        with closing(SqliteBillRepository(":memory:")) as target:
            target.migrate()
            before = target.dump()
            with pytest.raises(ValueError, match="invalid_amendment_input"):
                target.restore(script)
            assert target.dump() == before
    report = audit(path)
    assert any(item["code"] == "invalid_amendment_input" for item in report["findings"])


def test_intent_item_transport_refs_are_read_only_and_require_actual_question() -> None:
    value = input_snapshot()
    request = BatchRequest(
        custom_id="amendments",
        term=10,
        number="3039",
        question=AmendmentsQuestion(ctx=value.context),
        model="fixture",
    ).freeze(
        payload_json='{"frozen":"technical request"}',
        model="fixture",
        prompt_version="fixture",
        estimated_cost_usd=0,
    )
    assert request.payload_sha256 is not None, "the technical request was frozen locally"
    meta = DigestItemMeta(memo_key="amendments", request_sha256=request.payload_sha256)
    intent = BatchIntent.from_request(request=request, meta=meta, provider="openai", created_at=NOW)
    intent = BatchIntent.model_validate(
        {
            **intent.model_dump(),
            "meta": {
                **intent.meta.model_dump(),
                "amendment_input_ref": reference(value, request.payload_sha256),
            },
        }
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.save_batch_intent(intent)
        repo.save_amendment_input(value)
        repo.save_batch_intent(intent)
        assert repo.list_queued_batch_intents() == [intent]
        item = LlmBatchItem.from_intent("batch", intent)
        repo.save_llm_batch(
            LlmBatch(
                batch_id="batch",
                provider="openai",
                call_kind="amendments",
                submitted_at=NOW,
                status="submitted",
                request_count=1,
                estimated_cost_usd=0,
            ),
            [item],
        )
        assert repo.list_llm_batch_items("batch") == [item]
        dumped = repo.dump()
        repo.restore(dumped)
        assert repo.dump() == dumped
        wrong = intent.model_copy(
            update={
                "request": request.model_copy(
                    update={
                        "question": AmendmentsQuestion(
                            ctx=value.context.model_copy(update={"proposal": "changed"})
                        )
                    }
                )
            }
        )
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.save_batch_intent(wrong)


def test_none_carriers_preserve_exact_original_serialization_and_no_capture() -> None:
    result = retained_result("amendments")
    assert "amendment_input_ref" not in result.model_dump()
    assert "amendment_input_ref" not in DigestItemMeta(memo_key="legacy").model_dump()
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_computation_result(result)
        assert repo.get_computation_result(result.result_id) == result
        assert repo.get_amendment_input("amendment-input-v1:" + "0" * 64) is None
        encoded = result.model_dump_json()
        replacement = result.model_copy(
            update={
                "amendment_input_ref": reference(
                    input_snapshot(), result.request_sha256 or "0" * 64
                ),
                "input_binding": ComputationInputBinding(
                    term=10, number="3039", reuse_key="amendments"
                ),
            }
        )
        repo.save_amendment_input(input_snapshot())
        with pytest.raises(ValueError, match="immutable computation"):
            repo.save_computation_result(replacement)
        assert (
            repo.get_computation_result(result.result_id) == result
            and result.model_dump_json() == encoded
        )


def test_native_root_availability_survives_cleanup_without_current_bill_or_source_reads(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = input_snapshot()
    case.repo.save_amendment_input(value)
    result = paid_input(value)
    original = queued(case.repo, case.bill, case.change, result)
    assert poster(case.repo, FakePublisher()).deliver(original)
    confirmed = case.repo.publication_by_id(original.id or 0)
    assert confirmed is not None, "the native public poster froze the original edition"

    def forbidden_current_bill(term: int, number: str) -> None:
        raise AssertionError("historical input availability cannot consult the current Bill")

    monkeypatch.setattr(case.repo, "get", forbidden_current_bill)
    first = original_amendment_input(case.repo, confirmed)
    assert (
        first.state == "available"
        and first.snapshot == value
        and first.result_id == result.result_id
        and first.reference == result.amendment_input_ref
    )
    case.repo.prune_history(
        before=NOW + dt.timedelta(days=181), memo_before=NOW + dt.timedelta(days=181)
    )
    dumped = case.repo.dump()
    case.repo.restore(dumped)
    case.repo.restore(dumped)
    assert original_amendment_input(case.repo, confirmed) == first


def test_later_refetched_revision_never_backfills_root_original_input(case: Case) -> None:
    value = input_snapshot()
    original_result = paid_input(value, bound=False)
    original = queued(case.repo, case.bill, case.change, original_result)
    service = poster(case.repo, FakePublisher())
    assert service.deliver(original)
    confirmed = case.repo.publication_by_id(original.id or 0)
    assert confirmed is not None, "the original legacy input has a frozen edition"
    case.repo.save_amendment_input(value)
    candidate = paid_input(value).model_copy(update={"batch_id": "later-refetch"})
    assert isinstance(candidate.record, AmendmentsRecord), (
        "the later technical revision is an amendments result"
    )
    candidate = ComputationResult.model_validate(
        {
            **candidate.model_dump(),
            "record": candidate.record.model_copy(update={"computation_id": candidate.result_id}),
        }
    )
    case.repo.save_computation_result(candidate)
    changed = revision(case.repo, confirmed, candidate)
    assert service.deliver(changed)
    availability = original_amendment_input(case.repo, changed)
    assert availability.state == "unavailable" and availability.reason == "legacy_input_unavailable"
    assert availability.result_id == original_result.result_id
    assert case.repo.get_computation_result(candidate.result_id) == candidate


def test_old_receipt_is_lineage_not_a_fresh_request_binding() -> None:
    value = input_snapshot()
    context = value.context
    result = paid_input(value)
    assert result.request_sha256 is not None and result.amendment_input_ref is not None, (
        "the source fixture has its original binding"
    )
    receipt = AmendmentInputReceipt(
        context_sha256=amendment_context_sha256(context),
        request_sha256=result.request_sha256,
        text_sha256=hashlib.sha256(context.text.encode()).hexdigest(),
        text_chars=len(context.text),
        source_part=PreparedTextPart(
            text_sha256=hashlib.sha256(context.text.encode()).hexdigest(),
            chars=len(context.text),
            role="bill",
        ),
        retention="unknown",
        reason="projection_unavailable",
    )
    original_ref = result.amendment_input_ref.model_copy(update={"lineage_receipt": receipt})
    original = ComputationResult.model_validate(
        {**result.model_dump(), "amendment_input_ref": original_ref}
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_amendment_input(value)
        repo.save_computation_result(original)
        new_sha = "d" * 64
        with pytest.raises(ValueError):
            AmendmentInputRef(
                snapshot_id=value.snapshot_id,
                question_sha256=value.question_sha256,
                request_sha256=new_sha,
                lineage_receipt=receipt,
            )
        lineage = AmendmentInputRef(
            snapshot_id=value.snapshot_id,
            question_sha256=value.question_sha256,
            request_sha256=new_sha,
            lineage_receipt=receipt,
            lineage_result_id=original.result_id,
        )
        assert (
            lineage.lineage_receipt == receipt and receipt.request_sha256 != lineage.request_sha256
        )
        from_carrier = paid_input(value).model_copy(
            update={
                "batch_id": "new-binding",
                "request_sha256": new_sha,
                "amendment_input_ref": lineage,
            }
        )
        assert from_carrier.record is not None, "the replay has a validated paid record"
        replay = ComputationResult.model_validate(
            {
                **from_carrier.model_dump(),
                "record": from_carrier.record.model_copy(
                    update={"computation_id": from_carrier.result_id, "request_sha256": new_sha}
                ),
            }
        )
        repo.save_computation_result(replay)
        assert repo.get_computation_result(replay.result_id) == replay


@pytest.mark.parametrize("damage", ["request", "binding", "purpose"])
def test_known_reference_cannot_survive_an_unknown_or_different_carrier(damage: str) -> None:
    value = input_snapshot()
    original = paid_input(value)
    raw = original.model_dump(mode="json")
    if damage == "request":
        raw["request_sha256"] = None
    elif damage == "binding":
        raw["input_binding"] = None
    else:
        raw["purpose"] = "supplement"
    with pytest.raises(ValueError, match="invalid_amendment_input"):
        ComputationResult.model_validate(raw)


def technical_receipt(value: AmendmentInputSnapshot, request_sha256: str) -> AmendmentInputReceipt:
    return AmendmentInputReceipt(
        context_sha256=amendment_context_sha256(value.context),
        request_sha256=request_sha256,
        text_sha256=hashlib.sha256(value.context.text.encode()).hexdigest(),
        text_chars=len(value.context.text),
        source_part=PreparedTextPart(
            text_sha256=hashlib.sha256(value.context.text.encode()).hexdigest(),
            chars=len(value.context.text),
            role="bill",
        ),
        retention="unknown",
        reason="projection_unavailable",
    )


def test_derived_input_cannot_hide_a_broken_original_lineage_parent(tmp_path: Path) -> None:
    value = input_snapshot()
    original = paid_input(value)
    assert original.request_sha256 is not None and original.amendment_input_ref is not None, (
        "the technical original has its frozen request and input ref"
    )
    receipt = technical_receipt(value, original.request_sha256)
    original = ComputationResult.model_validate(
        {
            **original.model_dump(),
            "amendment_input_ref": original.amendment_input_ref.model_copy(
                update={"lineage_receipt": receipt}
            ),
        }
    )
    derived = original.model_copy(update={"batch_id": "derived", "request_sha256": "d" * 64})
    assert derived.record is not None, "the technical lineage has a validated original record"
    derived = ComputationResult.model_validate(
        {
            **derived.model_dump(),
            "record": derived.record.model_copy(
                update={"computation_id": derived.result_id, "request_sha256": "d" * 64}
            ),
            "amendment_input_ref": reference(value, "d" * 64).model_copy(
                update={"lineage_receipt": receipt, "lineage_result_id": original.result_id}
            ),
        }
    )
    path = tmp_path / "lineage.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_amendment_input(value)
        repo.save_computation_result(original)
        repo.save_computation_result(derived)
        raw = original.model_dump(mode="json")
        raw["amendment_input_ref"]["question_sha256"] = "0" * 64
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "UPDATE computation_results SET result_json=? WHERE result_id=?",
                (json.dumps(raw), original.result_id),
            )
            connection.commit()
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.get_computation_result(original.result_id)
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.get_computation_result(derived.result_id)
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.save_computation_result(derived)


def test_direct_lineage_must_match_the_actual_known_prepared_receipt() -> None:
    value = input_snapshot()
    request_sha = "a" * 64
    receipt = technical_receipt(value, request_sha)
    different = AmendmentInputReceipt.model_validate(
        {
            **receipt.model_dump(),
            "source_part": receipt.source_part.model_copy(update={"role": "justification"}),
        }
    )
    ref = reference(value, request_sha).model_copy(update={"lineage_receipt": receipt})
    binding = ComputationInputBinding(term=10, number="3039", reuse_key="amendments")
    item = LlmBatchItem(
        batch_id="direct",
        custom_id="direct",
        call_kind="amendments",
        term=10,
        number="3039",
        meta=DigestItemMeta(
            memo_key="amendments",
            request_sha256=request_sha,
            input_binding=binding,
            amendment_input_ref=ref,
            prepared_input_receipt=different,
        ),
    )
    batch = LlmBatch(
        batch_id="direct",
        provider="openai",
        call_kind="amendments",
        submitted_at=NOW,
        status="submitted",
        request_count=1,
        estimated_cost_usd=0,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_amendment_input(value)
        with pytest.raises(ValueError, match="invalid_amendment_input"):
            repo.save_llm_batch(batch, [item])


@pytest.mark.parametrize("blob", [False, True])
def test_damaged_snapshot_keys_cannot_leak_body_text_into_diagnostics(
    tmp_path: Path, blob: bool
) -> None:
    path = tmp_path / "private.db"
    private = "PRIVATE QUESTION BODY " * 300
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_amendment_input(input_snapshot())
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "UPDATE amendment_input_snapshots SET snapshot_id=?",
                (private.encode() if blob else private,),
            )
            connection.commit()
            findings = list(amendment_input_issues(connection))
        assert len(findings) == 1
        assert private not in str(findings) and len(findings[0][1]) < 100
    output = json.dumps(audit(path))
    assert private not in output


@pytest.mark.parametrize("known", [False, True])
def test_old_schema_without_table_reports_only_known_input_refs(
    tmp_path: Path, known: bool
) -> None:
    path = tmp_path / "old.db"
    value = input_snapshot()
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_amendment_input(value)
        repo.save_computation_result(paid_input(value, bound=known))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE amendment_input_snapshots")
        connection.execute("PRAGMA user_version=66")
        connection.commit()
    findings = [
        item for item in audit(path)["findings"] if item["code"] == "invalid_amendment_input"
    ]
    assert bool(findings) == known


def test_older_unavailable_layout_has_no_new_amendment_finding() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("PRAGMA user_version=1")
        assert list(amendment_input_issues(connection)) == []
