import datetime as dt

import pytest

import lexinform.services.documents as document_service
from lexinform.document_selection import REGULATION_SELECTION_POLICY_VERSION
from lexinform.models import BillStatus, process_summary
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, World, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.unit.test_regulation_versions import TEXT

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
def test_policy_retry_is_recorded_once_and_survives_restart(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        workers=workers,
        extractor=FakeTextExtractor(
            page_count=0, by_content={b"one": TEXT, b"two": TEXT.replace("200,50", "100,50")}
        ),
    )
    blocked_project(world)
    with monkeypatch.context() as legacy:
        legacy.setattr(
            document_service, "REGULATION_SELECTION_POLICY_VERSION", "regulation-selection-v1"
        )
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
