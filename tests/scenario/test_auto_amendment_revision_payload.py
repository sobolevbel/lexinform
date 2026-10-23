from dataclasses import dataclass
from pathlib import Path
from typing import Never

import pytest

from lexinform.errors import DeliveryUncertainError, TelegramUnavailableError
from lexinform.models import (
    Attachment,
    DeliveryPlan,
    DocumentWork,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
)
from lexinform.ports import PublishResult
from tests.fakes import FakeTextExtractor, make_amendments
from tests.harness import CHANNEL, REFERRED, TERM, World
from tests.scenario.test_card_remote_checkpoints import Mode, connect, git
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import restored
from tests.scenario.test_senate_document_selection import FIXTURES, STAGE, SUBJECT

MARKER = "Поздний разбор: сбор 200 PLN, повторное заявление не требуется."
URL = "https://api.test/3213.docx"


@dataclass(frozen=True)
class LateCase:
    world: World
    original: Publication
    waiting: DocumentWork
    revision: Publication


def row(world: World, identity: int) -> Publication:
    publication = world.repo.publication_by_id(identity)
    assert publication is not None, "the fixture keeps the original and revision identities"
    return publication


def plan(publication: Publication) -> DeliveryPlan:
    assert publication.delivery is not None, "the original event and correction have saved inputs"
    return publication.delivery


def queued(mode: Mode = "on", *, long: bool = False) -> LateCase:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = World(
        profile="production",
        reader_attention=mode,
        text_budget_chars=60_000,
        extractor=FakeTextExtractor(by_content={b"resolution": resolution}),
    )
    world.add_bill("1929", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    world.run()
    world.batch.resolve()
    world.run()
    frame = Stage(stage_type="CommitteeWork", stage_name="Prace w komisji")
    world.set_stages("1929", REFERRED + (frame,))
    world.clock.advance(days=1)
    world.run()
    world.gateway.prints["3213"] = PrintInfo(
        term=TERM,
        number="3213",
        title="Uchwała Senatu w sprawie ustawy " + SUBJECT,
        attachments=(Attachment(print_number="3213", name="3213.docx", url=URL),),
    )
    world.set_stages("1929", REFERRED + (frame, STAGE))
    world.clock.advance(days=1)
    world.run()
    change = world.repo.list_status_changes(TERM, "1929")[-1]
    assert change.id is not None, "tracking stored the original Senate event"
    original = world.repo.get_update_publication(change.id, CHANNEL)
    assert original is not None and original.id is not None, "tracking sent the bare event"
    assert original.status is PublicationStatus.SENT and original.delivery is not None, (
        "tracking sends the original event with its immutable semantic and HTML snapshot"
    )
    assert original.delivery.rendered_text and MARKER not in original.delivery.rendered_text
    assert original.delivery.held_change_ids, "the original owns its earlier held frame step"
    world.gateway.files[URL] = b"resolution"
    world.batch.secondary_script[("1929", "amendments")] = make_amendments(
        summary=MARKER + (" Новое условие для ранее поданных заявлений. " * 220 if long else "")
    )
    world.clock.advance(days=1)
    world.run()
    pending = world.repo.list_document_work(due=world.clock.now())
    assert len(pending) == 1 and pending[0].context_json is not None
    assert not world.llm.amendment_contexts, "production amendments wait for their fake batch"
    waiting = pending[0]
    world.batch.resolve()
    world.run()
    revisions = world.repo.list_amendment_revisions(original.id)
    assert len(revisions) == 1, "the completed batch creates one separate correction"
    revision = revisions[0]
    assert revision.id is not None and revision.status is PublicationStatus.QUEUED, (
        "the automatic recovery stores a separate correction before delivery"
    )
    return LateCase(world, original, waiting, revision)


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("an attempted revision must retain its frozen HTML")


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("long", [False, True])
def test_late_amendments_create_new_frozen_html_without_old_physical_membership_or_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, long: bool
) -> None:
    case = queued(mode, long=long)
    world = case.world
    original = plan(case.original)
    revision = case.revision
    assert revision.id is not None and case.original.id is not None, "both rows are stored"
    fresh = plan(revision)
    assert not fresh.rendered_text and not fresh.continuation_ids
    assert not fresh.publication_ids and not fresh.update_group and not fresh.sitting_group
    assert not fresh.held_change_ids and not fresh.digest_event_ids and not fresh.digest_parts
    assert fresh.continuation_root is None and fresh.continuation_parent is None
    assert fresh.destination == "direct" and fresh.absorbed_by is None
    assert fresh.bill_json == original.bill_json
    assert fresh.today == original.today and fresh.reply_to == original.reply_to
    assert fresh.edit_message_id == case.original.message_id
    assert fresh.revision_of == case.original.id
    revised_change = StatusChange.model_validate_json(fresh.change_json)
    assert revised_change.amendments is not None, (
        "the correction contains the collected batch result"
    )
    assert revised_change.amendments.amendments.summary.startswith(MARKER)
    remote = connect(world, tmp_path)
    prepared = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        assert publication.id is not None, "new physical correction parts retain their identity"
        saved = row(durable, publication.id)
        assert saved.status is PublicationStatus.PENDING and plan(saved) == publication.delivery
        assert row(durable, case.original.id or 0) == case.original
        assert not plan(saved).held_change_ids and not plan(saved).publication_ids
        if publication.id == revision.id:
            assert MARKER in plan(saved).rendered_text
            assert plan(saved).rendered_text != original.rendered_text
            assert plan(saved).edit_message_id == case.original.message_id
            assert bool(plan(saved).continuation_ids) is long
            for identity in plan(saved).continuation_ids:
                assert identity != case.original.id
                assert row(durable, identity).status is PublicationStatus.QUEUED
                assert plan(row(durable, identity)).continuation_root == revision.id
        observed.append(publication.id)
        return prepared(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert world.container.poster(dry_run=False).deliver(revision)

    durable = restored(remote, world, tmp_path)
    assert row(durable, case.original.id) == case.original
    assert row(durable, revision.id).status is PublicationStatus.SENT
    assert observed[0] == revision.id
    assert len(observed) > 1 if long else len(observed) == 1
    message, target = next(
        (message, target)
        for message, target in world.publisher.edited
        if message.kind is PublicationKind.AMENDMENTS_REVISION
    )
    assert target == case.original.message_id and MARKER in message.text
    assert len(world.batch.submitted) == 2 and not world.llm.amendment_contexts


@pytest.mark.parametrize(
    ("status", "attempts"),
    [
        (PublicationStatus.QUEUED, 0),
        (PublicationStatus.FAILED, 0),
        (PublicationStatus.FAILED, 1),
        (PublicationStatus.PENDING, 0),
        (PublicationStatus.UNKNOWN, 0),
    ],
)
def test_revisited_work_never_replaces_an_existing_or_attempted_correction(
    status: PublicationStatus,
    attempts: int,
) -> None:
    case = queued()
    world = case.world
    assert case.revision.id is not None, "the late batch queues its correction before retry work"
    if status is PublicationStatus.QUEUED:
        saved = case.revision
    else:
        if attempts:
            world.publisher.fail_on.add("1929")
            assert not world.container.poster(dry_run=False).deliver(case.revision)
            world.publisher.fail_on.clear()
        else:
            world.publisher.outage_on_edit.add("1929")
            with pytest.raises(TelegramUnavailableError):
                world.container.poster(dry_run=False).deliver(case.revision)
            world.publisher.outage_on_edit.clear()
        world.repo.mark_publication(case.revision.id, status, count_attempt=False)
        saved = row(world, case.revision.id)
        assert saved.attempts == attempts
        assert plan(saved).rendered_text, "the first failed attempt freezes the corrected HTML"
    world.repo.save_document_work(case.waiting)
    world.batch.secondary_script[("1929", "amendments")] = make_amendments(summary="Another result")
    submitted = list(world.batch.submitted)
    before = len(world.publisher.edited)

    world.container.tracking_service(dry_run=False).check_bill(world.bill("1929"), publish=True)

    assert row(world, case.revision.id) == saved
    assert world.repo.list_amendment_revisions(case.original.id or 0) == [saved]
    assert world.repo.list_document_work(due=world.clock.now()) == [case.waiting]
    assert world.batch.submitted == submitted and len(world.publisher.edited) == before
    assert row(world, case.original.id or 0) == case.original


def test_unknown_late_correction_retries_its_saved_new_html_and_exact_edit_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = queued()
    world = case.world
    assert case.revision.id is not None, "the collected batch created an immutable correction row"
    remote = connect(world, tmp_path)
    world.publisher.unknown_on_edit.add("1929")
    with pytest.raises(DeliveryUncertainError):
        world.container.poster(dry_run=False).deliver(case.revision)
    fresh = restored(remote, world, tmp_path)
    saved = row(fresh, case.revision.id)
    assert saved.status is PublicationStatus.UNKNOWN
    assert MARKER in plan(saved).rendered_text
    before = len(fresh.publisher.edited)
    fresh.container.delivery_service(dry_run=False).run()
    assert len(fresh.publisher.edited) == before
    incoming = fresh.command(f"/delivery {saved.id} retry")
    _commands_only(fresh)
    prepared = fresh.publisher.publish_prepared

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        history = durable.repo.delivery_resolutions(saved.id or 0)
        assert len(history) == 1 and history[0].command == incoming
        assert publication.delivery == saved.delivery
        return prepared(publication)

    monkeypatch.setattr(fresh.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(fresh.publisher, "publish_prepared", inspect)

    fresh.container.delivery_service(dry_run=False).run()

    assert row(fresh, case.revision.id).status is PublicationStatus.SENT
    assert row(fresh, case.original.id or 0) == case.original
    assert len(fresh.publisher.edited) == before + 1
    assert fresh.publisher.edited[-1][0].text == plan(saved).rendered_text
    assert fresh.publisher.edited[-1][1] == case.original.message_id
