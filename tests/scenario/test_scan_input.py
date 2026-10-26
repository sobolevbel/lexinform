import base64
import hashlib

import pytest

from lexinform.models import AnalysisRecord, BillContext, ScannedDocument, Triage
from lexinform.models.batch import BatchItemMeta
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.harness import World


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("batch", [False, True])
def test_exact_scan_input_survives_batch_collect_restore_and_does_not_retriage(
    workers: int, batch: bool
) -> None:
    world = World(
        extractor=FakeTextExtractor("", page_count=30),
        workers=workers,
        batch_only=batch,
        triage=True,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    ctx = world.batch.submitted[0].ctx if batch else world.llm.contexts[0]
    assert isinstance(ctx, BillContext) and ctx.scan is not None, (
        "the bill is analysed as PDF pages"
    )
    manifest = ctx.scan.scan_input
    assert manifest is not None, "the loader creates a manifest for a new scan"
    if batch:
        job = world.repo.list_open_llm_batches()[0]
        item = world.repo.list_llm_batch_items(job.batch_id)[0]
        assert isinstance(item.meta, BatchItemMeta), "the pending item is the primary analysis"
        assert item.meta.selection.scan_input == manifest
        world.repo.restore(world.repo.dump())
        world.batch.resolve()
        world.gateway.outages.add("download")
        world.run(track=False)
    record = world.bill("3039").analysis
    assert record is not None, "the synchronous or collected analysis completes"
    assert record.selection.scan_input == manifest and record.text_sha256 == manifest.source_sha256
    world.repo.restore(world.repo.dump())
    saved = world.bill("3039").analysis
    assert saved is not None and saved.selection.scan_input == manifest, "restore retains metadata"
    assert len(world.llm.triage_contexts) == 1


def test_rejected_scan_records_the_eight_pages_triage_actually_saw() -> None:
    world = World(
        extractor=FakeTextExtractor("", page_count=30),
        triage=True,
        triage_script={
            "3039": Triage(affects_foreigners=False, confidence=0.99, rationale="Nie dotyczy")
        },
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    record = world.bill("3039").analysis
    shown = world.llm.triage_contexts[0].scan
    assert record is not None and shown is not None, "triage creates a rejection from PDF pages"
    assert record.selection.scan_input == shown.scan_input
    assert record.selection.scan_input is not None, (
        "the rejected input still has its exact identity"
    )
    assert record.selection.scan_input.page_count == 8 and not world.llm.contexts


def test_metadata_does_not_change_the_existing_memo_and_batch_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = World(
        extractor=FakeTextExtractor("", page_count=30), profile="production", triage=True
    )
    current.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    current.run(track=False)
    original = TextLoader.load_scan

    def legacy(loader: TextLoader, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        scan = original(loader, url, cover_letter=cover_letter)
        return scan.model_copy(update={"scan_input": None}) if scan else None

    monkeypatch.setattr(TextLoader, "load_scan", legacy)
    old = World(extractor=FakeTextExtractor("", page_count=30), profile="production", triage=True)
    old.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    old.run(track=False)

    assert current.batch.submitted[0].custom_id == old.batch.submitted[0].custom_id


@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_reused_scan_preserves_its_paid_input_or_legacy_unknown(
    reject: bool, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        extractor=FakeTextExtractor("", page_count=30),
        triage=True,
        triage_script={
            "3039": Triage(affects_foreigners=not reject, confidence=0.99, rationale="Ocena")
        },
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    save, failed = world.repo.save_analysis, False
    load = TextLoader.load_scan

    def untraced(loader: TextLoader, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        scan = load(loader, url, cover_letter=cover_letter)
        return scan.model_copy(update={"scan_input": None}) if scan else None

    def interrupted(term: int, number: str, record: AnalysisRecord) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected save failure after the paid result")
        save(term, number, record)

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_analysis", interrupted)
        if legacy:
            patch.setattr(TextLoader, "load_scan", untraced)
        assert world.run(track=False).analysis_failures == 1
    original = world.llm.triage_contexts[0].scan if reject else world.llm.contexts[0].scan
    assert original is not None, "the successful paid call read the scan"
    expected = None if legacy else original.scan_input

    def reencoded(loader: TextLoader, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        scan = load(loader, url, cover_letter=cover_letter)
        if scan is None or scan.scan_input is None:
            return scan
        data = base64.b64decode(scan.data) + b"\n% rewritten PDF\n"
        manifest = scan.scan_input.model_copy(
            update={"input_sha256": hashlib.sha256(data).hexdigest(), "input_bytes": len(data)}
        )
        return ScannedDocument.model_validate(
            {
                **scan.model_dump(),
                "data": base64.b64encode(data).decode(),
                "scan_input": manifest.model_dump(),
            }
        )

    monkeypatch.setattr(TextLoader, "load_scan", reencoded)
    world.repo.restore(world.repo.dump())
    report = world.run(track=False)
    record = world.bill("3039").analysis
    assert report.analysis_failures == 0 and record is not None, (
        "the cached answer completes after restore"
    )
    assert record.selection.scan_input == expected
    assert len(world.llm.triage_contexts) == 1 and len(world.llm.contexts) == int(not reject)
