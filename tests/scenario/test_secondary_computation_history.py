from typing import Literal

import pytest

from lexinform.models import (
    AmendmentsRecord,
    AnalysisRecord,
    BatchIntent,
    BatchRequest,
    BatchResult,
    DigestItemMeta,
    JointRecord,
    NameRecord,
    SupplementRecord,
    TextDocument,
)
from lexinform.services.analysis import Waiting
from tests.fakes import FakeLlm
from tests.harness import World
from tests.scenario.test_amendments_command import published_world

Kind = Literal["amendments", "supplement", "joint", "name", "explanation"]
Record = AnalysisRecord | AmendmentsRecord | SupplementRecord | JointRecord | NameRecord
KINDS: tuple[Kind, ...] = ("amendments", "supplement", "joint", "name", "explanation")
URL = "https://api.test/secondary.pdf"


def prepared_world(*, workers: int = 1) -> World:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.add_bill("3040", "Projekt ustawy o obywatelstwie")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    world.gateway.files[URL] = b"%PDF-secondary"
    world.analysis.start_run()
    return world


def ask(world: World, kind: Kind) -> Record | Waiting | None:
    bill = world.bill("3039")
    if kind == "amendments":
        return world.analysis.summarize_amendments(
            bill, TextDocument(url=URL, kind="committee_amendments")
        )
    if kind == "supplement":
        return world.analysis.digest_supplement(
            bill,
            TextDocument(url=URL, kind="government_position"),
            number="3041",
            title="Stanowisko Rządu",
        )
    if kind == "joint":
        return world.analysis.compare_joint(bill, [world.bill("3040")])
    if kind == "name":
        return world.analysis.name_bill(bill)
    return world.analysis.explain_bill(bill)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("workers", [1, 4])
def test_secondary_answer_survives_collect_restore_reuse_and_operational_cleanup(
    kind: Kind, workers: int
) -> None:
    world = prepared_world(workers=workers)
    before = world.bill("3039")
    primary_applications = world.repo.list_result_applications(10, "3039")
    assert isinstance(ask(world, kind), Waiting)
    world.analysis.submit_queued_batches()
    request = world.batch.submitted[-1]
    world.batch.resolve()

    world.analysis.collect_batches()
    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    reused = ask(world, kind)

    assert isinstance(
        reused, (AnalysisRecord, AmendmentsRecord, SupplementRecord, JointRecord, NameRecord)
    ), "the collected purpose returns its paid record through memo"
    results = [
        row for row in world.repo.list_computation_results(10, "3039") if row.purpose == kind
    ]
    assert len(results) == 1
    result = results[0]
    assert result.record is not None, "the validated secondary answer has a normalized record"
    assert reused.computation_id == result.result_id == result.record.computation_id
    assert result.generation is None and result.schema_version == 2
    assert result.request_sha256 == result.record.request_sha256 == request.payload_sha256
    assert result.record.input_tokens == result.usage.batch_input == FakeLlm.ANALYSIS_TOKENS[0]
    assert reused.input_tokens == 0
    assert world.bill("3039").status == before.status
    assert world.bill("3039").analysis_generation == before.analysis_generation
    assert world.repo.list_result_applications(10, "3039") == primary_applications
    submitted = len(world.batch.submitted)
    if isinstance(result.record, (AmendmentsRecord, SupplementRecord)):
        assert result.record.source_url == URL
    if isinstance(result.record, SupplementRecord):
        assert result.record.number == "3041"
    world.run(track=False)
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())

    assert world.repo.get_computation_result(result.result_id) == result
    assert len(world.batch.submitted) == submitted
    assert not world.repo.load_analysis_memo()


@pytest.mark.parametrize(
    "fault", ["save_computation_result", "save_analysis_memo", "mark_llm_batch_item_consumed"]
)
def test_secondary_history_memo_and_consumed_checkpoint_roll_back_together(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = prepared_world()
    assert isinstance(ask(world, "joint"), Waiting)
    world.analysis.submit_queued_batches()
    world.batch.resolve()
    history = world.repo.list_computation_results(10, "3039")
    memo = world.repo.load_analysis_memo()

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected secondary checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, fault, crash)
        with pytest.raises(RuntimeError, match="injected secondary"):
            world.analysis.collect_batches()

    assert world.repo.list_computation_results(10, "3039") == history
    assert world.repo.load_analysis_memo() == memo
    assert world.repo.list_open_llm_batches()
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    world.analysis.collect_batches()

    assert (
        len(
            [
                row
                for row in world.repo.list_computation_results(10, "3039")
                if row.purpose == "joint"
            ]
        )
        == 1
    )
    assert isinstance(ask(world, "joint"), JointRecord)
    assert len(world.batch.submitted) == 3


def test_failed_secondary_keeps_original_usage_without_a_successful_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = prepared_world()
    assert isinstance(ask(world, "joint"), Waiting)
    world.analysis.submit_queued_batches()
    request = world.batch.submitted[-1]
    world.batch.resolve()

    def invalid(batch_id: str, kind: str = "analysis") -> list[BatchResult]:
        return [
            BatchResult(
                custom_id=request.custom_id,
                model="gpt-5.1",
                error="invalid answer",
                input_tokens=53,
                output_tokens=7,
            )
        ]

    monkeypatch.setattr(world.batch, "fetch_results", invalid)
    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())

    results = [
        row for row in world.repo.list_computation_results(10, "3039") if row.purpose == "joint"
    ]
    assert len(results) == 1
    assert results[0].validation == "error" and results[0].record is None
    assert results[0].error == "invalid answer"
    assert results[0].usage.batch_input == 53 and results[0].usage.batch_output == 7


@pytest.mark.parametrize("kind", KINDS)
def test_legacy_pending_secondary_does_not_reconstruct_absent_original_provenance(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = prepared_world()
    save = world.repo.save_batch_intent
    prepare = world.batch.prepare_request

    def legacy_request(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"payload_sha256": None})

    def legacy_intent(intent: BatchIntent) -> None:
        assert isinstance(intent.meta, DigestItemMeta), "the scenario queues only secondary work"
        old_meta = DigestItemMeta.model_validate(
            intent.meta.model_dump(exclude={"source_url", "document_number"})
        )
        save(intent.model_copy(update={"meta": old_meta}))

    monkeypatch.setattr(world.repo, "save_batch_intent", legacy_intent)
    monkeypatch.setattr(world.batch, "prepare_request", legacy_request)
    assert isinstance(ask(world, kind), Waiting)
    world.analysis.submit_queued_batches()
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    world.gateway.outages.add("download")

    world.analysis.collect_batches()

    result = next(
        row for row in world.repo.list_computation_results(10, "3039") if row.purpose == kind
    )
    assert result.record is not None, "the old pending item supplies a validated provider answer"
    assert result.record.computation_id == result.result_id
    assert result.request_sha256 is None and result.record.request_sha256 is None
    if isinstance(result.record, (AmendmentsRecord, SupplementRecord)):
        assert result.record.source_url == ""
    if isinstance(result.record, SupplementRecord):
        assert result.record.number == ""


def test_amendment_revision_delivery_and_retained_answer_share_the_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, original = published_world()
    assert original.id is not None, "the published amendment has a stable edit target"
    world.command("/amendments 1929")
    world.run(track=False)
    world.batch.resolve()
    memo = world.repo.load_analysis_memo()

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected revision history failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_computation_result", crash)
        with pytest.raises(RuntimeError, match="injected revision"):
            world.analysis.collect_batches()

    assert not world.repo.list_amendment_revisions(original.id)
    assert not world.repo.list_computation_results(10, "1929")
    assert world.repo.load_analysis_memo() == memo
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    world.analysis.collect_batches()

    revisions = world.repo.list_amendment_revisions(original.id)
    assert len(revisions) == 1
    delivery = revisions[0].delivery
    assert delivery is not None, "the revision freezes its retained amendment answer for delivery"
    result = world.repo.list_computation_results(10, "1929")[0]
    assert result.record is not None, "the revision has one immutable paid result"
    assert result.result_id in delivery.change_json
    assert len(world.batch.submitted) == 1
