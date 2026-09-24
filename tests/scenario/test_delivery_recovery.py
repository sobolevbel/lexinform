from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from lexinform.models import (
    Bill,
    DeliveryPlan,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
)
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, COMMITTEE_STAGES, World
from tests.scenario.test_commands import TITLE, _commands_only


def interrupted_update(*, accepted: bool = False) -> tuple[World, int]:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    if not accepted:
        w.publisher.fail_on.add("3039")
    w.run()
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.id is not None and pub.delivery is not None
    w.repo.mark_publication(pub.id, PublicationStatus.PENDING)
    w.publisher.fail_on.clear()
    w.repo.restore(w.repo.dump())
    w.run()
    unknown = w.repo.publication_by_id(pub.id)
    assert unknown is not None and unknown.status is PublicationStatus.UNKNOWN
    return w, pub.id


def test_confirm_an_accepted_delivery_does_not_send_again() -> None:
    w, identity = interrupted_update(accepted=True)
    incoming = w.command(f"/delivery {identity} confirm 777")

    report = w.run()
    w.command(f"/delivery {identity} confirm 777")
    w.run()

    assert report.commands_failed == 0
    assert len(w.publisher.updates) == 1
    pub = w.repo.publication_by_id(identity)
    assert pub is not None and pub.status is PublicationStatus.SENT and pub.message_id == 777
    audit = w.repo.delivery_resolutions(identity)
    assert len(audit) == 1 and audit[0].command == incoming
    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR


def test_retry_uses_saved_snapshot_and_parent_once_after_restart() -> None:
    w, identity = interrupted_update()
    pub = w.repo.publication_by_id(identity)
    assert pub is not None and pub.delivery is not None
    before = w.formatter.delivery_preview(pub)
    old = w.bill("3039").analysis
    assert old is not None
    w.repo.save_analysis(
        10,
        "3039",
        old.model_copy(
            update={
                "analysis": old.analysis.model_copy(
                    update={"summary": "Changed after the failed delivery"}
                )
            }
        ),
    )
    card = w.publication("3039")
    assert card is not None and card.id is not None
    w.repo.mark_publication(card.id, PublicationStatus.SENT, message_id=999)
    w.command(f"/delivery {identity} retry")
    queued = _commands_only(w)
    w.command(f"/delivery {identity} retry")
    _commands_only(w)
    w.repo.restore(w.repo.dump())

    restarted = replace(w.container).pipeline(dry_run=False)
    w.pipeline = restarted
    report = w.run()
    repeated = w.run()

    assert queued.commands_failed == 0
    assert (report.updates, repeated.updates) == (1, 0)
    bill, change, reply = w.publisher.updates[0]
    assert reply == pub.delivery.reply_to and reply != 999
    assert w.formatter.status_update(bill, change).text == before
    assert len(w.repo.delivery_resolutions(identity)) == 1


def test_dismiss_closes_without_claiming_delivery_or_retrying() -> None:
    w, identity = interrupted_update()
    w.command(f"/delivery {identity} dismiss deadline has passed")

    report = w.run()
    w.run()

    assert not report.commands_failed and not w.publisher.updates
    pub = w.repo.publication_by_id(identity)
    assert pub is not None and pub.status is PublicationStatus.DISMISSED
    assert pub.sent_at is None
    assert w.repo.delivery_resolutions(identity)[0].reason == "deadline has passed"


def test_delivery_view_includes_snapshot_and_does_not_mutate() -> None:
    w, identity = interrupted_update()
    incoming = w.command(f"/delivery {identity}")

    _commands_only(w)

    outcome = w.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.DELIVERY and outcome.delivery is not None
    text = w.formatter.command_reply(incoming, outcome).text
    assert f"publication {identity}" in text and "Saved snapshot" in text
    assert not w.repo.delivery_resolutions(identity)
    assert outcome.delivery.status is PublicationStatus.UNKNOWN


@pytest.mark.parametrize("missing", ["payload", "parent"])
def test_retry_refuses_incomplete_legacy_deliveries(missing: str) -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    identity = w.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            channel_id=CHANNEL,
            kind=PublicationKind.ACT_PUBLISHED,
            status=PublicationStatus.UNKNOWN,
            created_at=w.clock.now(),
            delivery=DeliveryPlan(bill_json=w.bill("3039").model_dump_json())
            if missing == "parent"
            else None,
        )
    )
    w.command(f"/delivery {identity} retry")

    _commands_only(w)

    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert not w.repo.delivery_resolutions(identity)


def test_retry_respects_no_publish_and_other_channels() -> None:
    w, identity = interrupted_update()
    w.command(f"/delivery {identity} retry")
    _commands_only(w, publish=False)
    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert not w.repo.delivery_resolutions(identity)
    foreign = w.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            channel_id="@elsewhere",
            kind=PublicationKind.ACT_PUBLISHED,
            status=PublicationStatus.UNKNOWN,
            created_at=w.clock.now(),
        )
    )
    w.command(f"/delivery {foreign} confirm 777")
    _commands_only(w)
    foreign_outcome = w.replier.replies[-1][1]
    assert foreign_outcome.status is OutcomeStatus.NOT_FOUND
    assert not w.repo.delivery_resolutions(foreign)


def test_deleted_publication_id_is_never_reused() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    old = w.publication("3039")
    assert old is not None and old.id is not None
    w.repo.delete_publication(10, "3039", PublicationKind.NEW_BILL, CHANNEL)
    w.repo.restore(w.repo.dump())
    w.run()

    new = w.publication("3039")
    assert new is not None and new.id is not None and new.id > old.id
    assert w.repo.publication_by_id(old.id) is None


@pytest.mark.parametrize("accepted", [True, False])
def test_a_second_crash_requires_a_new_operator_decision(
    accepted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, identity = interrupted_update()
    w.command(f"/delivery {identity} retry")
    _commands_only(w)
    send = w.publisher.publish_status_update

    def crash(bill: Bill, change: StatusChange, reply_to: int | None) -> PublishResult:
        if accepted:
            send(bill, change, reply_to)
        raise KeyboardInterrupt("process stopped without recording the result")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_status_update", crash)
        with pytest.raises(KeyboardInterrupt):
            w.run()
    w.repo.restore(w.repo.dump())

    w.run()
    w.run()

    pub = w.repo.publication_by_id(identity)
    assert pub is not None and pub.status is PublicationStatus.UNKNOWN
    assert len(w.publisher.updates) == int(accepted)
    assert len(w.repo.delivery_resolutions(identity)) == 1


@pytest.mark.parametrize("action", ["confirm 777", "dismiss no longer useful"])
def test_resolving_a_card_does_not_recreate_it(action: str) -> None:
    w = World(fail_publish={"3039"})
    w.add_bill("3039", TITLE)
    w.run()
    card = w.publication("3039")
    assert card is not None and card.id is not None
    w.repo.mark_publication(card.id, PublicationStatus.UNKNOWN)
    w.publisher.fail_on.clear()
    w.command(f"/delivery {card.id} {action}")

    w.run()
    w.run()

    assert not w.publisher.new_bills
    assert len(w.repo.delivery_resolutions(card.id)) == 1


def test_confirmation_releases_only_the_saved_held_changes() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    ids: list[int] = []
    publications: list[int] = []
    for fingerprint in ("included", "later", "delivery"):
        change = StatusChange(
            term=10,
            number="3039",
            new_fingerprint=fingerprint,
            old_fingerprint=None,
            new_stages=[],
            detected_at=w.clock.now(),
        )
        identity = w.repo.add_status_change(change)
        assert identity is not None
        ids.append(identity)
        change.id = identity
        publications.append(
            w.repo.create_publication(
                Publication(
                    term=10,
                    number="3039",
                    channel_id=CHANNEL,
                    kind=PublicationKind.STATUS_UPDATE,
                    status=PublicationStatus.UNKNOWN
                    if fingerprint == "delivery"
                    else PublicationStatus.SKIPPED,
                    status_change_id=identity,
                    created_at=w.clock.now(),
                    delivery=DeliveryPlan(
                        bill_json=w.bill("3039").model_dump_json(),
                        change_json=change.model_dump_json(),
                        held_change_ids=(ids[0],),
                        reply_to=w.card_id("3039"),
                    )
                    if fingerprint == "delivery"
                    else None,
                )
            )
        )
    w.command(f"/delivery {publications[-1]} confirm 777")

    _commands_only(w)

    included = w.repo.get_update_publication(ids[0], CHANNEL)
    later = w.repo.get_update_publication(ids[1], CHANNEL)
    assert included is not None and included.status is PublicationStatus.SENT
    assert included.message_id == 777
    assert later is not None and later.status is PublicationStatus.SKIPPED
    assert not w.publisher.updates


def test_recovery_dry_run_rolls_back_status_and_audit() -> None:
    w, identity = interrupted_update()
    w.command(f"/delivery {identity} retry")

    _commands_only(w, dry_run=True)

    pub = w.repo.publication_by_id(identity)
    assert pub is not None and pub.status is PublicationStatus.UNKNOWN
    assert not w.repo.delivery_resolutions(identity)
    assert w.inbox.commands
    assert not w.publisher.updates


def test_confirmation_and_audit_roll_back_if_releasing_changes_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w, identity = interrupted_update()
    w.command(f"/delivery {identity} confirm 777")

    def fail_release(
        ids: tuple[int, ...], channel_id: str, *, message_id: int, sent_at: datetime
    ) -> None:
        raise RuntimeError("checkpoint interrupted")

    monkeypatch.setattr(w.repo, "release_planned_changes", fail_release)
    _commands_only(w)

    pub = w.repo.publication_by_id(identity)
    assert pub is not None and pub.status is PublicationStatus.UNKNOWN
    assert not w.repo.delivery_resolutions(identity)


def test_retry_reminder_preserves_its_date_and_does_not_send_twice() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    today = w.clock.now().date()
    hearing = Stage(
        stage_type="PublicHearing",
        stage_name="Wysłuchanie publiczne",
        date=today + timedelta(days=10),
    )
    identity = w.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.HEARING_DEADLINE,
            ref=hearing.date.isoformat() if hearing.date else None,
            status=PublicationStatus.UNKNOWN,
            channel_id=CHANNEL,
            created_at=w.clock.now(),
            delivery=DeliveryPlan(
                bill_json=w.bill("3039").model_dump_json(),
                today=today,
                hearing_json=hearing.model_dump_json(),
                reply_to=w.card_id("3039"),
            ),
        )
    )
    w.clock.advance(days=2)
    w.command(f"/delivery {identity} retry")

    report = w.run()
    repeated = w.run()

    assert (report.hearing_reminders, repeated.hearing_reminders) == (1, 0)
    assert w.publisher.hearings[0][3] == today


def test_retry_card_uses_its_saved_analysis() -> None:
    w = World(fail_publish={"3039"})
    w.add_bill("3039", TITLE)
    w.run()
    pub = w.publication("3039")
    assert pub is not None and pub.id is not None and pub.delivery is not None
    w.repo.mark_publication(pub.id, PublicationStatus.UNKNOWN)
    original = Bill.model_validate_json(pub.delivery.bill_json)
    analysis = w.bill("3039").analysis
    assert analysis is not None
    w.repo.save_analysis(
        10,
        "3039",
        analysis.model_copy(
            update={"analysis": analysis.analysis.model_copy(update={"summary": "New facts"})}
        ),
    )
    w.publisher.fail_on.clear()
    w.command(f"/delivery {pub.id} retry")

    report = w.run()
    repeated = w.run()

    assert (report.published, repeated.published) == (1, 0)
    assert w.publisher.new_bills[0][0].analysis == original.analysis
