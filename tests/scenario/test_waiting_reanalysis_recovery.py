import pytest

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.models import BillStatus, ObservationBasis, ObservationMode
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, World, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.scenario.test_document_selection_contract import law
from tests.unit.test_physical_extraction import archive, docx


def restart(world: World, workers: int) -> World:
    restarted = World(workers=workers, batch=True, extractor=world.extractor)
    restarted.clock.current = world.clock.now()
    for project in world.rcl.projects.values():
        restarted.add_rcl_project(project)
    restarted.rcl.files.update(world.rcl.files)
    restarted.batch.batches.update(world.batch.batches)
    restarted.batch.resolved.update(world.batch.resolved)
    restarted.repo.restore(world.repo.dump())
    return restarted


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("observation", ["card", "full", "metadata"])
@pytest.mark.parametrize("conflicting", [False, True])
def test_incomplete_reanalysis_remains_retryable_beyond_the_watermark(
    workers: int, observation: str, conflicting: bool
) -> None:
    after = law(day=2, days=60)
    reader = DocumentTextExtractor(
        FakeTextExtractor(after, page_count=1), DocxTextExtractor(), FakeTextExtractor()
    )
    world = World(workers=workers, batch=True, extractor=reader)
    limit = 100 if observation == "card" else 0
    original = rcl_document(9201, "Projekt ustawy.docx")
    stage = rcl_stage(
        9, "Stały Komitet Rady Ministrów", "reached", rcl_folder(9, "Projekt", original)
    )
    project = world.add_rcl_project(rcl_project(stages=(stage,), consultation=None))
    world.rcl.files[original.url] = docx(law())
    world.run(max_publish=limit)
    world.batch.resolve()
    world.run(max_publish=limit)
    previous = world.bill(RCL).analysis
    assert previous is not None, "the complete initial draft is analyzed before the update"
    world.clock.advance(days=2)
    modified = world.clock.now().date()
    revision = rcl_document(9202, "Projekt ustawy.zip", created=modified)
    updated = rcl_stage(10, "Komisja Prawnicza", "active").model_copy(
        update={"loose_documents": (revision,), "modified": modified}
    )
    world.add_rcl_project(
        project.model_copy(update={"modified": modified, "stages": (stage, updated)})
    )
    payload = b"%PDF-failed"
    complete = archive({"projekt.docx": docx(after), "projekt-wariant.pdf": payload})
    world.rcl.files[revision.url] = complete.replace(payload, b"%PDF-broken", 1)
    blocked = world.run(max_publish=limit)
    latest = world.repo.list_source_observations(10, RCL)[-1]
    selection = world.repo.get_document_selection(latest.selection_id)
    assert selection is not None, "the partial ZIP read records its selection"
    assert selection.selection.state == "waiting"
    assert world.bill(RCL).analysis == previous and blocked.batch_requests_submitted == 0
    if observation == "metadata":
        world.repo.set_observation(10, RCL, ObservationMode.METADATA, ObservationBasis.OPERATOR)
    world = restart(world, workers)
    since = world.clock.now()

    still_waiting = world.run(since=since, max_publish=limit)

    assert world.bill(RCL).analysis == previous and still_waiting.batch_requests_submitted == 0
    assert (f"download:{revision.url}" in world.rcl.calls) == (observation != "metadata")
    world = restart(world, workers)
    world.rcl.files[revision.url] = (
        archive({"projekt.docx": docx(after), "projekt-wariant.docx": docx(law(day=2, days=90))})
        if conflicting
        else complete
    )

    recovered = world.run(since=since, max_publish=limit)

    current = world.bill(RCL)
    assert current.analysis == previous and recovered.reanalyzed == 0
    latest = world.repo.list_source_observations(10, RCL)[-1]
    selection = world.repo.get_document_selection(latest.selection_id)
    assert selection is not None, "the selection survives a fresh process and database restore"
    if observation == "metadata":
        assert selection.selection.state == "waiting" and recovered.batch_requests_submitted == 0
        assert not any(call.startswith("download:") for call in world.rcl.calls)
        return
    if conflicting:
        assert selection.selection.state == "ambiguous" and recovered.batch_requests_submitted == 0
    else:
        assert selection.selection.state not in {"waiting", "ambiguous", "unsupported"}
        assert (
            current.status is BillStatus.BATCH_PENDING and recovered.batch_requests_submitted == 1
        )
        world = restart(world, workers)
        assert world.run(since=since, max_publish=limit).batch_requests_submitted == 0
        world.batch.resolve()
        collected = world.run(since=since, max_publish=limit)
        applied = world.bill(RCL).analysis
        assert applied is not None, "batch collection replaces the saved analysis"
        assert collected.reanalyzed == 1 and applied.source_url == revision.url
    world = restart(world, workers)

    repeated = world.run(since=since, max_publish=limit)

    assert repeated.reanalyzed == repeated.batch_requests_submitted == 0
    assert not any(call.startswith("download:") for call in world.rcl.calls)
