import datetime as dt

import pytest

import lexinform.regulation_versions as regulation_versions
import lexinform.services.documents as document_service
from lexinform.document_selection import REGULATION_SELECTION_POLICY_VERSION
from lexinform.document_versions import draft_version
from lexinform.models import BillStatus, ObservationBasis, ObservationMode, process_summary
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, World, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.unit.test_regulation_versions import FINAL, TEXT

TITLE = "Projekt rozporządzenia Ministra w sprawie opłat"


def blocked_project(world: World) -> None:
    first = rcl_document(9101, "Projekt.pdf")
    second = rcl_document(9102, "Projekt.docx")
    project = rcl_project(
        title=TITLE,
        keywords=(),
        areas=(),
        stages=(rcl_stage(2, "Uzgodnienia", "active", rcl_folder(12, "Projekt", first, second)),),
    )
    world.add_rcl_project(project)
    world.rcl.files[first.url] = b"one"
    world.rcl.files[second.url] = b"two"


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("policy", ["regulation-selection-v1", "regulation-selection-v2"])
def test_policy_retry_is_recorded_once_and_survives_restart(
    workers: int, policy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        workers=workers,
        extractor=FakeTextExtractor(
            page_count=0, by_content={b"one": TEXT, b"two": TEXT.replace("200,50", "100,50")}
        ),
    )
    blocked_project(world)
    with monkeypatch.context() as legacy:
        legacy.setattr(document_service, "REGULATION_SELECTION_POLICY_VERSION", policy)
        first = world.run(max_analyze=0)
    world.repo.restore(world.repo.dump())

    retried = world.run(max_analyze=0)
    world.repo.restore(world.repo.dump())
    repeated = world.run(max_analyze=0)

    assert first.text_prefilter_ambiguous == retried.text_prefilter_ambiguous == 1
    assert first.text_prefilter_unreadable == retried.text_prefilter_unreadable == 0
    assert world.bill(RCL).status is BillStatus.DOCUMENT_AMBIGUOUS
    assert repeated.text_prefilter_checked == 0
    latest = world.repo.list_source_observations(10, RCL)[-1]
    selection = world.repo.get_document_selection(latest.selection_id)
    assert selection is not None, "the prefilter records the selected policy atomically"
    assert selection.selection.policy_version == REGULATION_SELECTION_POLICY_VERSION
    assert not world.llm.contexts and not world.publisher.new_bills


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize("observation", ["card", "full", "metadata"])
def test_superseded_reanalysis_retries_beyond_the_discovery_watermark_once(
    workers: int, conflicting: bool, observation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        workers=workers,
        batch=True,
        extractor=FakeTextExtractor(
            page_count=0,
            by_content={b"old": TEXT, b"new": FINAL, b"variant": FINAL.replace("200,50", "300,50")},
        ),
    )
    limit = 100 if observation == "card" else 0
    original = rcl_document(9101, "Projekt.docx")
    stage = rcl_stage(12, "Rada Ministrów", "reached", rcl_folder(12, "Projekt", original))
    project = world.add_rcl_project(rcl_project(title=TITLE, stages=(stage,)))
    world.rcl.files[original.url] = b"old"
    world.run(max_publish=limit)
    world.batch.resolve()
    world.run(max_publish=limit)
    previous = world.bill(RCL).analysis
    assert previous is not None, "the initial run analyzes the unambiguous draft"
    world.clock.advance(days=2)
    modified = world.clock.now().date()
    revision = rcl_document(9102, "Projekt rozporządzenia.docx", created=modified)
    variant = rcl_document(9103, "Projekt rozporządzenia wariant.docx", created=modified)
    signing = rcl_stage(14, "Skierowanie projektu do podpisu Prezesa Rady Ministrów", "active")
    signing = signing.model_copy(
        update={
            "loose_documents": (revision, variant) if conflicting else (revision,),
            "modified": modified,
        }
    )
    world.add_rcl_project(
        project.model_copy(update={"modified": modified, "stages": (stage, signing)})
    )
    world.rcl.files.update({revision.url: b"new", variant.url: b"variant"})
    with monkeypatch.context() as legacy:
        legacy.setattr(
            document_service, "REGULATION_SELECTION_POLICY_VERSION", "regulation-selection-v2"
        )
        legacy.setattr(regulation_versions, "regulation_version", draft_version)
        world.run(max_publish=limit)
    assert world.bill(RCL).analysis == previous
    blocked = world.repo.list_source_observations(10, RCL)[-1]
    blocked_selection = world.repo.get_document_selection(blocked.selection_id)
    assert blocked_selection is not None, "failed preparation retains its selection metadata"
    world.repo.save_source_observation(
        blocked_selection, blocked.model_copy(update={"purpose": "review"})
    )
    if observation == "metadata":
        world.repo.set_observation(10, RCL, ObservationMode.METADATA, ObservationBasis.OPERATOR)
    restarted = World(workers=workers, batch=True, extractor=world.extractor)
    restarted.clock.current = world.clock.now()
    for project in world.rcl.projects.values():
        restarted.add_rcl_project(project)
    restarted.rcl.files.update(world.rcl.files)
    restarted.batch.batches.update(world.batch.batches)
    restarted.batch.resolved.update(world.batch.resolved)
    restarted.repo.restore(world.repo.dump())
    world = restarted
    since = world.clock.now()
    if observation != "metadata":
        history = world.repo.list_source_observations(10, RCL)
        world.rcl.outages.add("download")
        unavailable = world.run(since=since, max_publish=limit)
        world.rcl.outages.clear()
        assert unavailable.errors
        assert unavailable.batch_requests_submitted == 0
        assert world.bill(RCL).analysis == previous
        assert world.repo.list_source_observations(10, RCL) == history
        world.repo.restore(world.repo.dump())
    world.rcl.calls.clear()

    recovered = world.run(since=since, max_publish=limit)
    current = world.bill(RCL)
    latest = world.repo.list_source_observations(10, RCL)[-1]
    selection = world.repo.get_document_selection(latest.selection_id)

    assert selection is not None, "the retry records its selection before preparing paid work"
    if observation == "metadata":
        assert selection.selection.policy_version == "regulation-selection-v2"
        assert current.analysis == previous and recovered.reanalyzed == 0
        assert not any(call.startswith("download") for call in world.rcl.calls)
        return
    assert selection.selection.policy_version == REGULATION_SELECTION_POLICY_VERSION
    assert current.analysis is not None, "both recovery and continued ambiguity retain an analysis"
    assert recovered.reanalyzed == 0 and current.analysis == previous
    if conflicting:
        assert recovered.batch_requests_submitted == 0
    else:
        assert recovered.batch_requests_submitted == 1
        assert current.status is BillStatus.BATCH_PENDING
        world.repo.restore(world.repo.dump())
        assert world.run(since=since, max_publish=limit).batch_requests_submitted == 0
        world.batch.resolve()
        applied = world.run(since=since, max_publish=limit)
        current = world.bill(RCL)
        assert current.analysis is not None, "the collected reanalysis replaces the old record"
        assert applied.reanalyzed == 1 and current.analysis.source_url == revision.url
    world.repo.restore(world.repo.dump())
    world.rcl.calls.clear()
    repeated = world.run(since=since, max_publish=limit)

    assert repeated.reanalyzed == 0
    assert not any(call.startswith("download") for call in world.rcl.calls)
    assert repeated.document_reads is not None, "the pipeline captures reads even on a quiet run"
    assert repeated.document_reads.cache_hits == repeated.document_reads.completed == 0
    assert world.repo.list_source_observations(10, RCL)[-1] == latest


@pytest.mark.parametrize("workers", [1, 4])
def test_changed_rcl_source_reopens_a_blocked_project_without_replaying_history(
    workers: int,
) -> None:
    world = World(
        workers=workers,
        extractor=FakeTextExtractor(
            page_count=0,
            by_content={
                b"one": TEXT,
                b"two": TEXT.replace("200,50", "100,50"),
                b"new": TEXT + " Cudzoziemiec składa wniosek. Cudzoziemiec otrzymuje zezwolenie.",
            },
        ),
    )
    blocked_project(world)
    world.run(max_analyze=0)
    project = world.bill(RCL).rcl
    assert project is not None, "blocked_project creates an RCL-backed row"
    world.clock.advance(days=1)
    changed = world.clock.now().date()
    draft = rcl_document(9103, "Projekt nowa wersja.docx", created=changed)
    world.rcl.put(
        project.model_copy(
            update={
                "modified": changed,
                "stages": (
                    rcl_stage(
                        2,
                        "Uzgodnienia",
                        "active",
                        rcl_folder(12, "Projekt", draft),
                        modified=changed,
                    ),
                ),
            }
        )
    )
    world.rcl.files[draft.url] = b"new"

    world.run(max_analyze=0)
    world.repo.restore(world.repo.dump())
    recovered = world.run(max_analyze=0)

    assert recovered.text_prefilter_hits == 1
    assert world.bill(RCL).status is BillStatus.ANALYSIS_PENDING
    assert not world.publisher.new_bills and not world.llm.contexts


def test_signing_stage_uses_its_explicit_rm_package_instead_of_earlier_variants() -> None:
    world = World(extractor=FakeTextExtractor(TEXT, page_count=0))
    older = rcl_document(9101, "Projekt.zip")
    final = rcl_document(9102, "RM-06110-68-26.zip", created=dt.date(2026, 9, 2))
    stage = rcl_stage(14, "Skierowanie projektu do podpisu Prezesa Rady Ministrów", "active")
    stage = stage.model_copy(update={"loose_documents": (final,)})
    project = rcl_project(
        title=TITLE,
        keywords=(),
        areas=(),
        stages=(
            rcl_stage(12, "Rada Ministrów", "reached", rcl_folder(12, "Projekt", older)),
            stage,
        ),
    )
    world.add_rcl_project(project)
    world.rcl.files[final.url] = b"final"

    world.run(max_analyze=0)

    observation = world.repo.list_source_observations(10, RCL)[-1]
    assert observation.document.url == final.url
    assert not any(older.url in call for call in world.rcl.calls)


def test_legacy_blocked_row_without_selection_history_is_retried_once() -> None:
    world = World(
        extractor=FakeTextExtractor(
            page_count=0, by_content={b"one": TEXT, b"two": TEXT.replace("200,50", "100,50")}
        )
    )
    blocked_project(world)
    project = next(iter(world.rcl.projects.values()))
    world.repo.upsert_summary(process_summary(project, term=10), now=world.clock.now())
    world.repo.save_rcl(10, RCL, project)
    world.repo.set_status(10, RCL, BillStatus.DOCUMENT_AMBIGUOUS)

    retried = world.run(max_analyze=0)
    world.repo.restore(world.repo.dump())
    repeated = world.run(max_analyze=0)

    assert retried.text_prefilter_ambiguous == 1
    assert repeated.text_prefilter_checked == 0
    assert world.bill(RCL).status is BillStatus.DOCUMENT_AMBIGUOUS


def test_source_retry_outage_preserves_the_blocked_decision() -> None:
    world = World(
        extractor=FakeTextExtractor(
            page_count=0, by_content={b"one": TEXT, b"two": TEXT.replace("200,50", "100,50")}
        )
    )
    blocked_project(world)
    world.run(max_analyze=0)
    bill = world.bill(RCL)
    world.clock.advance(days=1)
    world.repo.upsert_summary(
        bill.summary.model_copy(update={"change_date": world.clock.now()}), now=world.clock.now()
    )
    world.rcl.outages.add("get_project")
    observations = world.repo.list_source_observations(10, RCL)

    report = world.run(max_analyze=0)

    assert world.bill(RCL).status is BillStatus.DOCUMENT_AMBIGUOUS
    assert world.repo.list_source_observations(10, RCL) == observations
    assert report.text_prefilter_checked == 0
    assert not world.publisher.new_bills and not world.llm.contexts
