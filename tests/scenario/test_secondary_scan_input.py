import base64
import hashlib
from typing import Literal

import pytest

from lexinform.models import (
    AmendmentsRecord,
    BatchIntent,
    DigestItemMeta,
    ScannedDocument,
    SupplementRecord,
    TextDocument,
)
from lexinform.services.analysis import Waiting
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.harness import World

Kind = Literal["amendments", "supplement"]
URL = "https://api.test/secondary.pdf"
DATA = b"%PDF-secondary-scan" * 100


def ready_world(*, batch: bool = True, workers: int = 1) -> World:
    world = World(
        profile="production" if batch else "default",
        extractor=FakeTextExtractor(by_content={DATA: ""}, page_count=3),
        workers=workers,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    if batch:
        world.batch.resolve()
        world.run(track=False)
    assert world.bill("3039").analysis is not None, "the primary analysis completed first"
    world.gateway.files[URL] = DATA
    world.analysis.start_run()
    return world


def ask(world: World, kind: Kind) -> AmendmentsRecord | SupplementRecord | Waiting | None:
    bill = world.bill("3039")
    if kind == "amendments":
        return world.analysis.summarize_amendments(
            bill, TextDocument(url=URL, kind="committee_amendments")
        )
    return world.analysis.digest_supplement(
        bill,
        TextDocument(url=URL, kind="government_position"),
        number="3040",
        title="Stanowisko Rządu",
    )


@pytest.mark.parametrize("kind", ["amendments", "supplement"])
@pytest.mark.parametrize("workers", [1, 4])
def test_secondary_scan_manifest_survives_frozen_batch_restore_and_collect(
    kind: Kind, workers: int
) -> None:
    world = ready_world(workers=workers)
    original_calls = len(world.batch.submitted)
    assert isinstance(ask(world, kind), Waiting)
    intent = world.repo.list_queued_batch_intents()[0]
    assert isinstance(intent.meta, DigestItemMeta), "secondary work uses digest metadata"
    expected = world.container.text_loader().load_scan(URL, cover_letter=False)
    assert expected is not None and expected.scan_input is not None, "the new scan is traced"
    assert intent.meta.scan_input == expected.scan_input
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    world.analysis.submit_queued_batches()
    world.batch.resolve()
    world.gateway.outages.add("download")

    world.analysis.collect_batches()
    first_memos = world.repo.load_analysis_memo()
    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())

    assert world.repo.load_analysis_memo() == first_memos
    raw = first_memos[intent.meta.memo_key]
    record = (
        AmendmentsRecord.model_validate_json(raw)
        if kind == "amendments"
        else SupplementRecord.model_validate_json(raw)
    )
    assert record.scan_input == expected.scan_input
    assert record.input_tokens is not None and record.input_tokens > 0, (
        "the collected fake provider result retains its original paid usage"
    )
    assert len(world.batch.submitted) == original_calls + 1
    assert not world.llm.amendment_contexts and not world.llm.supplement_contexts


@pytest.mark.parametrize("kind", ["amendments", "supplement"])
@pytest.mark.parametrize("legacy", [False, True])
def test_secondary_memo_reuse_keeps_paid_manifest_or_legacy_unknown(
    kind: Kind, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = ready_world()
    load = TextLoader.load_scan

    def untraced(loader: TextLoader, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        scan = load(loader, url, cover_letter=cover_letter)
        return scan.model_copy(update={"scan_input": None}) if scan else None

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(TextLoader, "load_scan", untraced)
        assert isinstance(ask(world, kind), Waiting)
    intent = world.repo.list_queued_batch_intents()[0]
    assert isinstance(intent.meta, DigestItemMeta), "secondary work uses digest metadata"
    expected = intent.meta.scan_input
    assert (expected is None) == legacy
    world.analysis.submit_queued_batches()
    world.batch.resolve()
    world.analysis.collect_batches()
    submitted = len(world.batch.submitted)
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()

    def reencoded(loader: TextLoader, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        scan = load(loader, url, cover_letter=cover_letter)
        if scan is None or scan.scan_input is None:
            return scan
        data = base64.b64decode(scan.data) + b"\n% new serialization\n"
        manifest = scan.scan_input.model_copy(
            update={"input_sha256": hashlib.sha256(data).hexdigest(), "input_bytes": len(data)}
        )
        return ScannedDocument.model_validate(
            {**scan.model_dump(), "data": base64.b64encode(data).decode(), "scan_input": manifest}
        )

    monkeypatch.setattr(TextLoader, "load_scan", reencoded)

    reused = ask(world, kind)

    assert isinstance(reused, (AmendmentsRecord, SupplementRecord)), "the saved answer is reused"
    assert reused.scan_input == expected and reused.input_tokens == 0
    assert len(world.batch.submitted) == submitted and not world.repo.list_queued_batch_intents()
    assert not world.llm.amendment_contexts and not world.llm.supplement_contexts
    if legacy:
        assert "scan_input" not in reused.model_dump()


def test_synchronous_supplement_retains_input_in_answer_and_memo() -> None:
    world = ready_world(batch=False)

    answer = ask(world, "supplement")
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    reused = ask(world, "supplement")

    assert isinstance(answer, SupplementRecord) and isinstance(reused, SupplementRecord), (
        "the synchronous call and its saved memo both return supplement records"
    )
    shown = world.llm.supplement_contexts[0].scan
    assert shown is not None and shown.scan_input is not None, "the call reads a traced scan"
    assert answer.scan_input == reused.scan_input == shown.scan_input
    assert reused.input_tokens == 0 and len(world.llm.supplement_contexts) == 1


@pytest.mark.parametrize("kind", ["amendments", "supplement"])
def test_secondary_text_records_and_bare_supplements_do_not_gain_scan_fields(kind: Kind) -> None:
    world = ready_world(batch=False)
    assert isinstance(world.extractor, FakeTextExtractor), "the scenario controls extracted text"
    world.extractor.by_content[DATA] = world.extractor.text

    answer = ask(world, kind)
    bare = world.analysis.bare_supplement(
        TextDocument(url=URL, kind="government_position"), number="3040", title="Stanowisko"
    )

    assert isinstance(answer, (AmendmentsRecord, SupplementRecord)), "readable text is analysed"
    assert answer.scan_input is None and "scan_input" not in answer.model_dump()
    assert bare.scan_input is None and "scan_input" not in bare.model_dump()
    assert "scan_input" not in DigestItemMeta(memo_key="legacy").model_dump()


@pytest.mark.parametrize("kind", ["amendments", "supplement"])
def test_pre_manifest_pending_batch_collects_without_inventing_scan_history(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = ready_world()
    save = world.repo.save_batch_intent

    def legacy_intent(intent: BatchIntent) -> None:
        assert isinstance(intent.meta, DigestItemMeta), "the test queues only secondary work"
        old_meta = DigestItemMeta.model_validate(intent.meta.model_dump(exclude={"scan_input"}))
        save(intent.model_copy(update={"meta": old_meta}))

    monkeypatch.setattr(world.repo, "save_batch_intent", legacy_intent)
    assert isinstance(ask(world, kind), Waiting)
    intent = world.repo.list_queued_batch_intents()[0]
    assert isinstance(intent.meta, DigestItemMeta), "the restored work is a secondary request"
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    world.analysis.submit_queued_batches()
    world.batch.resolve()
    world.gateway.outages.add("download")

    world.analysis.collect_batches()

    raw = world.repo.load_analysis_memo()[intent.meta.memo_key]
    record = (
        AmendmentsRecord.model_validate_json(raw)
        if kind == "amendments"
        else SupplementRecord.model_validate_json(raw)
    )
    assert record.scan_input is None and "scan_input" not in record.model_dump()
