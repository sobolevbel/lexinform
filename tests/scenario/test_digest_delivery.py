import datetime as dt

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    Bill,
    Editorial,
    PriorityOverride,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from tests.harness import CHANNEL, COMMITTEE_STAGES, World


def queued_event(w: World, number: str = "3039") -> Publication:
    w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages(number, COMMITTEE_STAGES)
    w.run(publish=False)
    pub = w.publication(number, PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.id is not None and pub.delivery is not None
    plan = pub.delivery.model_copy(
        update={
            "destination": "digest_only",
            "destination_reason": "test enrichment",
            "digest_eligible_at": w.clock.now(),
            "schedule": None,
            "wait_until": None,
        }
    )
    assert w.repo.replace_unattempted_delivery(pub.id, plan)
    w.clock.advance(hours=1)
    return pub.model_copy(update={"delivery": plan})


def test_digest_only_event_is_never_drained_and_draft_is_not_coverage() -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.id is not None
    before = len(w.publisher.sent)

    w.container.delivery_service(dry_run=False).run()
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    service.draft("2026-W37")

    current = w.repo.publication_by_id(source.id)
    assert current is not None and current.status is PublicationStatus.QUEUED
    assert w.repo.digest_owner(source.id) is None
    assert len(w.publisher.texts(PublicationKind.STATUS_UPDATE)) == 0
    assert len(w.publisher.sent) > before


def test_unapproved_digest_waiting_stays_saved_without_reports_or_repeated_alerts() -> None:
    w = World(reader_attention="on", attention_roundup="daily")
    source = queued_event(w)
    assert source.id is not None
    calls = len(w.notifier.calls)

    first = w.container.delivery_service(dry_run=False).run()
    w.repo.restore(w.repo.dump())
    w.clock.advance(hours=25)
    second = w.container.delivery_service(dry_run=False).run()

    assert first.is_empty and second.is_empty
    assert second.attention["digest_queued_events"] == 1
    assert second.attention["digest_awaiting_approval"] == 1
    assert len(w.notifier.calls) == calls
    assert w.repo.get_delivery_alert(CHANNEL) is None
    assert w.repo.publication_by_id(source.id) == source
    text = w.formatter.run_report(second, []).text
    assert "digest_" not in text and "attention: delivery" not in text


def test_approved_digest_waiting_keeps_delivery_alerts() -> None:
    w = World(reader_attention="on")
    queued_event(w)
    w.publisher.fail_on = {DIGEST_NUMBER}
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    assert service.publish("2026-W37").failed
    calls = len(w.notifier.calls)

    report = w.container.delivery_service(dry_run=False).run()

    assert not report.is_empty
    assert report.attention["digest_awaiting_approval"] == 0
    assert len(w.notifier.calls) == calls + 1
    assert w.repo.get_delivery_alert(CHANNEL) is not None
    assert "digest_queued_events: 1" in w.formatter.run_report(report, []).text


def test_approved_digest_covers_exact_saved_event_and_survives_restore() -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.id is not None
    w.repo.restore(w.repo.dump())
    service = w.container.digest_service(dry_run=False)
    assert service is not None

    result = service.publish("2026-W37")
    repeated = service.publish("2026-W37")

    current = w.repo.publication_by_id(source.id)
    owner_id = w.repo.digest_owner(source.id)
    assert owner_id is not None
    owner = w.repo.publication_by_id(owner_id)
    assert owner is not None and owner.delivery is not None
    assert current is not None and current.status is PublicationStatus.SENT
    assert current.message_id == owner.message_id == result.message_id
    assert result.reader_parts == 1 and result.covered_events == 1
    assert owner.delivery.digest_event_ids == (source.id,)
    assert str(source.number) in owner.delivery.rendered_text
    assert not repeated.published
    assert w.publisher.texts(PublicationKind.STATUS_UPDATE) == []
    w.repo.prune_history(before=w.clock.now() + dt.timedelta(days=1), memo_before=w.clock.now())
    assert w.repo.digest_owner(source.id) == owner_id
    assert service.build("2026-W37").updates == ()


@pytest.mark.parametrize("action", ["confirm 777", "retry", "dismiss checked absence"])
def test_unknown_digest_part_owns_events_until_operator_resolution(action: str) -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.id is not None
    w.publisher.fail_on = {DIGEST_NUMBER}
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    assert service.publish("2026-W37").failed
    owner_id = w.repo.digest_owner(source.id)
    assert owner_id is not None
    before = w.repo.publication_by_id(owner_id)
    assert before is not None and before.delivery is not None
    w.repo.mark_publication(owner_id, PublicationStatus.UNKNOWN, count_attempt=False)
    w.repo.restore(w.repo.dump())
    w.publisher.fail_on.clear()

    assert service.publish("2026-W37").failed
    assert service.build("2026-W37").updates == ()
    w.command(f"/delivery {owner_id} {action}")
    w.run(discover=False, analyze=False, track=False)

    source_now = w.repo.publication_by_id(source.id)
    owner = w.repo.publication_by_id(owner_id)
    assert owner is not None and source_now is not None
    if action.startswith("confirm"):
        assert source_now.status is PublicationStatus.SENT and source_now.message_id == 777
    elif action.startswith("dismiss"):
        assert source_now.status is PublicationStatus.DISMISSED
    else:
        service.run()
        owner = w.repo.publication_by_id(owner_id)
        source_now = w.repo.publication_by_id(source.id)
        assert owner is not None and source_now is not None
        assert source_now.status is PublicationStatus.SENT
        assert owner.delivery == before.delivery


def test_failed_digest_source_cannot_be_reassigned_to_a_second_week() -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.id is not None
    w.publisher.fail_on = {DIGEST_NUMBER}
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    service.publish("2026-W37")
    first_owner = w.repo.digest_owner(source.id)

    service.publish("2026-W38")

    assert w.repo.digest_owner(source.id) == first_owner
    assert service.build("2026-W38").updates == ()
    owner = w.repo.get_publication(
        DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, CHANNEL, ref="2026-W38"
    )
    assert owner is not None


@pytest.mark.parametrize("unknown", [False, True])
def test_one_digest_owns_every_event_of_its_week_and_confirms_them_only_when_sent(
    unknown: bool,
) -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.delivery is not None
    snapshot = Bill.model_validate_json(source.delivery.bill_json)
    assert snapshot.analysis is not None
    change = StatusChange.model_validate_json(source.delivery.change_json)
    sources = [source.id]
    for index in range(25):
        number = str(4000 + index)
        summary = snapshot.summary.model_copy(update={"number": number})
        analysis = snapshot.analysis.analysis.model_copy(
            update={
                "summary": "Проект предлагает изменить правила для иностранцев со старыми заявлениями и подтверждёнными основаниями пребывания в Польше и для новых заявителей с отдельными условиями рассмотрения документов."
            }
        )
        bill = snapshot.model_copy(
            update={
                "summary": summary,
                "analysis": snapshot.analysis.model_copy(update={"analysis": analysis}),
            }
        )
        w.repo.upsert_summary(summary, now=w.clock.now())
        event = change.model_copy(
            update={"number": number, "new_fingerprint": f"split-{number}", "id": None}
        )
        identity = w.repo.add_status_change(event)
        assert identity is not None
        event.id = identity
        plan = source.delivery.model_copy(
            update={"bill_json": bill.model_dump_json(), "change_json": event.model_dump_json()}
        )
        sources.append(
            w.repo.create_publication(
                source.model_copy(
                    update={
                        "id": None,
                        "number": number,
                        "status_change_id": identity,
                        "delivery": plan,
                    }
                )
            )
        )
    if unknown:
        w.publisher.unknown_digest_attempt = w.publisher.digest_send_attempts + 1
    else:
        w.publisher.fail_digest_attempt = w.publisher.digest_send_attempts + 1
    service = w.container.digest_service(dry_run=False)
    assert service is not None

    if unknown:
        with pytest.raises(DeliveryUncertainError):
            service.publish("2026-W37")
    else:
        assert service.publish("2026-W37").failed

    # The message shows eight of the 26 and still owns them all: the rest are covered unshown.
    owners = {w.repo.digest_owner(identity) for identity in sources if identity is not None}
    assert None not in owners and len(owners) == 1
    for identity in sources:
        assert identity is not None
        owner_id = w.repo.digest_owner(identity)
        assert owner_id is not None
        owner = w.repo.publication_by_id(owner_id)
        event_post = w.repo.publication_by_id(identity)
        assert owner is not None and event_post is not None
        assert event_post.status is (
            PublicationStatus.SENT
            if owner.status is PublicationStatus.SENT
            else PublicationStatus.QUEUED
        )
    w.repo.restore(w.repo.dump())
    w.repo.prune_history(before=w.clock.now() + dt.timedelta(days=1), memo_before=w.clock.now())
    if not unknown:
        service.run()
        assert all(
            (post := w.repo.publication_by_id(identity)) is not None
            and post.status is PublicationStatus.SENT
            for identity in sources
            if identity is not None
        )
    else:
        before_count = len(w.publisher.sent)
        service.run()
        assert len(w.publisher.sent) == before_count


@pytest.mark.parametrize("reserved", [False, True])
def test_operator_override_only_releases_unreserved_events(reserved: bool) -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.id is not None
    if reserved:
        w.publisher.fail_on = {DIGEST_NUMBER}
        service = w.container.digest_service(dry_run=False)
        assert service is not None
        service.publish("2026-W37")
    w.repo.save_editorial(
        10,
        "3039",
        Editorial(
            priority=PriorityOverride(
                important=True,
                reason="new evidence",
                expires_at=w.clock.now() + dt.timedelta(days=1),
                selected_at=w.clock.now(),
                command_update_id=12,
            )
        ),
    )

    report = w.container.delivery_service(dry_run=False).run()

    current = w.repo.publication_by_id(source.id)
    assert current is not None and current.delivery is not None
    if reserved:
        assert current.delivery.destination == "digest_only"
        assert report.updates == 0
    else:
        assert current.delivery.destination == "direct"
        assert report.updates == 1


def test_a_dry_run_does_not_release_or_reserve_digest_work() -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.id is not None
    w.repo.save_editorial(
        10,
        "3039",
        Editorial(
            priority=PriorityOverride(
                important=True,
                reason="new evidence",
                expires_at=w.clock.now() + dt.timedelta(days=1),
                selected_at=w.clock.now(),
                command_update_id=12,
            )
        ),
    )
    before = w.repo.publication_by_id(source.id)

    w.container.delivery_service(dry_run=False).run(dry_run=True)

    assert w.repo.publication_by_id(source.id) == before
    assert w.repo.digest_owner(source.id) is None
