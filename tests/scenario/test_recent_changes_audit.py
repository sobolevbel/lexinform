import datetime as dt

import pytest

from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from tests.fakes import make_analysis
from tests.formatting import assert_telegram_html
from tests.harness import CHANNEL, COMMITTEE_STAGES, REFERRED, TERM, World
from tests.scenario.test_daily_attention import queued_world
from tests.scenario.test_digest_delivery import queued_event


def digest_chain(w: World, source_id: int | None = None) -> tuple[int, ...]:
    identities: list[int] = []
    for index in range(3):
        identities.append(
            w.repo.create_publication(
                Publication(
                    term=DIGEST_TERM,
                    number=DIGEST_NUMBER,
                    kind=PublicationKind.DIGEST,
                    channel_id=CHANNEL,
                    status=PublicationStatus.QUEUED,
                    ref="2026-W37" if index == 0 else f"2026-W37:part:{index + 1}",
                    created_at=w.clock.now(),
                    delivery=DeliveryPlan(
                        bill_json="",
                        rendered_text=f"Saved digest part {index + 1}",
                        digest_ref="2026-W37",
                        digest_event_ids=(source_id,)
                        if index == 2 and source_id is not None
                        else (),
                    ),
                )
            )
        )
    root = w.repo.publication_by_id(identities[0])
    assert root is not None and root.delivery is not None
    assert w.repo.replace_unattempted_delivery(
        identities[0], root.delivery.model_copy(update={"digest_parts": tuple(identities)})
    )
    if source_id is not None:
        w.repo.reserve_digest_events(identities[-1], (source_id,))
    return tuple(identities)


@pytest.mark.parametrize("blocked_index", [0, 1, 2])
def test_digest_resume_respects_each_physical_parts_attempt_limit(blocked_index: int) -> None:
    w = World(reader_attention="on")
    identities = digest_chain(w)
    for identity in identities[:blocked_index]:
        w.repo.mark_publication(
            identity, PublicationStatus.SENT, message_id=700 + identity, sent_at=w.clock.now()
        )
    for _ in range(3):
        w.repo.mark_publication(
            identities[blocked_index],
            PublicationStatus.FAILED,
            error="definite rejection",
            count_attempt=True,
        )
    w.repo.restore(w.repo.dump())
    service = w.container.digest_service(dry_run=False)
    assert service is not None

    service.run()

    assert w.publisher.digest_send_attempts == 0
    blocked = w.repo.publication_by_id(identities[blocked_index])
    assert blocked is not None and blocked.attempts == 3
    assert blocked.status is PublicationStatus.FAILED


@pytest.mark.parametrize("blocked_index", [0, 1])
@pytest.mark.parametrize("action", ["confirm 888", "retry", "dismiss checked absence"])
def test_resolving_a_digest_part_leaves_no_unrecoverable_tail(
    blocked_index: int, action: str
) -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.id is not None
    identities = digest_chain(w, source.id)
    for identity in identities[:blocked_index]:
        w.repo.mark_publication(
            identity, PublicationStatus.SENT, message_id=700 + identity, sent_at=w.clock.now()
        )
    w.repo.mark_publication(
        identities[blocked_index], PublicationStatus.UNKNOWN, count_attempt=False
    )
    w.command(f"/delivery {identities[blocked_index]} {action}")

    w.run(discover=False, analyze=False, track=False)
    w.repo.restore(w.repo.dump())
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    service.run()

    current = w.repo.publication_by_id(source.id)
    assert current is not None
    assert current.status in (PublicationStatus.SENT, PublicationStatus.DISMISSED)
    assert w.repo.digest_owner(source.id) == identities[-1]
    if action.startswith("dismiss"):
        assert current.status is PublicationStatus.DISMISSED
        assert w.repo.delivery_resolutions(identities[-1])
    assert not any(
        part is not None and part.status is PublicationStatus.QUEUED
        for identity in identities[blocked_index + 1 :]
        for part in (w.repo.publication_by_id(identity),)
    )


@pytest.mark.parametrize("last_part", [False, True])
def test_dismissed_continuation_finishes_its_absorbed_change(last_part: bool) -> None:
    w = queued_world()
    absorbed = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert absorbed is not None and absorbed.id is not None
    analysis = w.bill("3039").analysis
    assert analysis is not None
    w.repo.save_analysis(
        TERM,
        "3039",
        analysis.model_copy(
            update={
                "analysis": analysis.analysis.model_copy(
                    update={"summary": "Материальное изменение для старых заявлений. " * 500}
                )
            }
        ),
    )
    change = StatusChange(
        term=TERM,
        number="3039",
        detected_at=w.clock.now(),
        old_fingerprint="old",
        new_fingerprint="audit-material",
        new_stages=[],
        content_changed=True,
    )
    poster = w.container.poster(dry_run=False)
    recorded = poster.record_change(change)
    assert recorded is not None and recorded.id is not None
    plan = poster.prepare(w.bill("3039"), recorded)
    assert absorbed.status_change_id in plan.held_change_ids
    queued = w.repo.get_update_publication(recorded.id, CHANNEL)
    assert queued is not None
    total = len(w.publisher.split_message(w.publisher.render_delivery(queued))) - 1
    assert total >= 2
    w.publisher.unknown_continuation_attempt = total if last_part else 1

    assert poster.status_update(w.bill("3039"), recorded)
    root = w.repo.get_update_publication(recorded.id, CHANNEL)
    assert root is not None and root.delivery is not None
    identities = root.delivery.continuation_ids
    blocked = identities[-1] if last_part else identities[0]
    w.command(f"/delivery {blocked} dismiss checked absence")
    w.run(discover=False, analyze=False, track=False)
    w.repo.restore(w.repo.dump())

    current = w.repo.publication_by_id(absorbed.id)
    assert current is not None and current.status is PublicationStatus.DISMISSED


def test_completed_multipart_card_updates_its_current_stage() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    analysis = make_analysis()
    analysis.key_changes = ["200 PLN, nie 300 PLN; stare wnioski. " * 150]
    w.llm.script["3039"] = analysis
    w.run()
    root = w.publication("3039")
    assert root is not None and root.delivery is not None and root.delivery.continuation_ids
    assert all(
        part is not None and part.status is PublicationStatus.SENT
        for identity in root.delivery.continuation_ids
        for part in (w.repo.publication_by_id(identity),)
    )

    w.clock.advance(days=1)
    w.set_stages("3039", COMMITTEE_STAGES)
    report = w.run()

    assert report.cards_refreshed == 1


def test_digest_membership_survives_payload_retention() -> None:
    w = World(reader_attention="on")
    source = queued_event(w)
    assert source.id is not None
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    service.publish("2026-W37")
    owner = w.repo.digest_owner(source.id)
    assert owner is not None

    w.repo.prune_history(before=w.clock.now() + dt.timedelta(days=1), memo_before=w.clock.now())
    w.repo.restore(w.repo.dump())

    assert w.repo.digest_owner(source.id) == owner
    assert service.build("2026-W37").updates == ()


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("attempt", [1, 2])
def test_unknown_continuation_is_reported_in_its_first_run(workers: int, attempt: int) -> None:
    w = World(reader_attention="on", workers=workers)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    analysis = make_analysis()
    analysis.practical_impact = "Материальное изменение для старых заявлений. " * 500
    w.llm.script["3039"] = analysis
    w.publisher.unknown_continuation_attempt = attempt

    report = w.run()

    root = w.publication("3039")
    assert root is not None and root.delivery is not None
    part = w.repo.publication_by_id(root.delivery.continuation_ids[attempt - 1])
    assert part is not None and part.status is PublicationStatus.UNKNOWN
    assert not report.ok
    assert any(f"/delivery {part.id}" in error for error in report.errors)
    assert report.published == 1
    assert report.message_parts_sent == attempt - 1


@pytest.mark.parametrize("blocked_index", [0, 1, 2])
def test_exhausted_digest_requires_an_audited_manual_retry(blocked_index: int) -> None:
    w = World(reader_attention="on")
    identities = digest_chain(w)
    for identity in identities[:blocked_index]:
        w.repo.mark_publication(identity, PublicationStatus.SENT, message_id=700 + identity)
    for _ in range(3):
        w.repo.mark_publication(
            identities[blocked_index], PublicationStatus.FAILED, error="rejected"
        )
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    service.publish("2026-W37")
    assert w.publisher.digest_send_attempts == 0

    w.command(f"/delivery {identities[blocked_index]} retry")
    w.run(discover=False, analyze=False, track=False)
    w.repo.restore(w.repo.dump())
    service.run()

    assert all(
        part is not None and part.status is PublicationStatus.SENT
        for identity in identities
        for part in (w.repo.publication_by_id(identity),)
    )
    assert len(w.repo.delivery_resolutions(identities[blocked_index])) == 1


@pytest.mark.parametrize("size", [10, 1000])
def test_multipart_card_refresh_preserves_full_text_after_retention(size: int) -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    analysis = make_analysis()
    analysis.key_changes = ["200 PLN, не 300 PLN; старые заявления. " * 150]
    w.llm.script["3039"] = analysis
    w.run()
    card = w.publication("3039")
    assert card is not None and card.id is not None
    original = w.repo.card_refresh_parts(card.id)
    assert len(original) > 1
    w.repo.prune_history(before=w.clock.now() + dt.timedelta(days=1), memo_before=w.clock.now())
    w.repo.restore(w.repo.dump())
    record = w.bill("3039").analysis
    assert record is not None
    updated = record.analysis.model_copy(update={"key_changes": ["Новая сумма 400 PLN. " * size]})
    w.repo.save_analysis(TERM, "3039", record.model_copy(update={"analysis": updated}))

    report = w.run(discover=False, analyze=False)

    assert report.cards_refreshed == 1
    current = w.repo.card_refresh_parts(card.id)
    assert all(part.status is PublicationStatus.SENT for part in current)
    assert len(current) >= len(original)
    info = w.gateway.get_print(TERM, "3039")
    expected = w.publisher.render_card_parts(w.bill("3039"), info, minimum=len(original))
    assert tuple(part.delivery.rendered_text for part in current if part.delivery) == expected
    for text in expected:
        assert_telegram_html(text)
    assert [part.message_id for part in current[: len(original)]] == [
        part.message_id for part in original
    ]
    assert w.run(discover=False, analyze=False).cards_refreshed == 0


def test_multipart_card_refresh_recovers_from_a_failed_edit_after_restart() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    analysis = make_analysis()
    analysis.key_changes = ["200 PLN, не 300 PLN. " * 300]
    w.llm.script["3039"] = analysis
    w.run()
    card = w.publication("3039")
    assert card is not None and card.id is not None
    w.publisher.fail_on.add("3039")
    w.set_stages("3039", COMMITTEE_STAGES)
    report = w.run()
    assert report.cards_refreshed == 0
    failed = w.repo.card_refresh_parts(card.id)
    assert failed[0].status is PublicationStatus.FAILED
    saved = [part.delivery for part in failed]
    w.repo.restore(w.repo.dump())
    w.publisher.fail_on.clear()

    report = w.run()

    assert report.cards_refreshed == 1
    assert all(part.status is PublicationStatus.SENT for part in w.repo.card_refresh_parts(card.id))
    assert [
        part.delivery.rendered_text for part in w.repo.card_refresh_parts(card.id) if part.delivery
    ] == [plan.rendered_text for plan in saved if plan]


@pytest.mark.parametrize("action", ["retry", "confirm 888"])
def test_multipart_card_refresh_unknown_append_keeps_saved_edits(action: str) -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    analysis = make_analysis()
    analysis.key_changes = ["200 PLN, не 300 PLN. " * 300]
    w.llm.script["3039"] = analysis
    w.run()
    card = w.publication("3039")
    assert card is not None and card.id is not None
    record = w.bill("3039").analysis
    assert record is not None
    analysis = record.analysis.model_copy(update={"key_changes": ["400 PLN. " * 3000]})
    w.repo.save_analysis(TERM, "3039", record.model_copy(update={"analysis": analysis}))
    w.publisher.unknown_continuation_attempt = w.publisher.continuation_attempts + 1

    report = w.run(discover=False, analyze=False)

    assert not report.ok and report.cards_refreshed == 0
    parts = w.repo.card_refresh_parts(card.id)
    unknown = next(part for part in parts if part.status is PublicationStatus.UNKNOWN)
    assert unknown.id is not None
    saved = [part.delivery.rendered_text for part in parts if part.delivery]
    before = len(w.publisher.edited)
    w.repo.restore(w.repo.dump())
    w.run(discover=False, analyze=False)
    assert len(w.publisher.edited) == before
    w.command(f"/delivery {unknown.id} {action}")

    report = w.run(discover=False, analyze=False)

    assert report.cards_refreshed == 1
    finished = w.repo.card_refresh_parts(card.id)
    assert all(part.status is PublicationStatus.SENT for part in finished)
    assert saved == [part.delivery.rendered_text for part in finished if part.delivery]
    assert len(w.publisher.edited) == before


@pytest.mark.parametrize("drain", [False, True])
def test_tracking_continuation_unknown_reports_the_sent_root(drain: bool) -> None:
    w = queued_world()
    record = w.bill("3039").analysis
    assert record is not None
    analysis = record.analysis.model_copy(update={"summary": "Материальное изменение. " * 1000})
    w.repo.save_analysis(TERM, "3039", record.model_copy(update={"analysis": analysis}))
    change = StatusChange(
        term=TERM,
        number="3039",
        detected_at=w.clock.now(),
        old_fingerprint="old",
        new_fingerprint="audit-report",
        new_stages=[],
        content_changed=True,
    )
    poster = w.container.poster(dry_run=False)
    recorded = poster.record_change(change)
    assert recorded is not None and recorded.id is not None
    poster.prepare(w.bill("3039"), recorded)
    w.clock.advance(days=2)
    w.publisher.unknown_continuation_attempt = 2

    report = (
        w.container.delivery_service(dry_run=False).run()
        if drain
        else w.run(discover=False, analyze=False)
    )

    root = w.repo.get_update_publication(recorded.id, CHANNEL)
    assert root is not None and root.status is PublicationStatus.SENT and root.delivery
    part = w.repo.publication_by_id(root.delivery.continuation_ids[1])
    assert part is not None and part.status is PublicationStatus.UNKNOWN
    assert not report.ok
    assert report.updates == 1 and report.message_parts_sent == 1
    assert any(f"/delivery {part.id}" in error for error in report.errors)


def test_digest_dismiss_preserves_already_confirmed_events_and_membership() -> None:
    w = World(reader_attention="on")
    earlier = queued_event(w, "3039")
    later = queued_event(w, "3040")
    assert earlier.id is not None and later.id is not None
    identities = digest_chain(w, later.id)
    root = w.repo.publication_by_id(identities[0])
    assert root is not None and root.delivery is not None
    assert w.repo.replace_unattempted_delivery(
        identities[0], root.delivery.model_copy(update={"digest_event_ids": (earlier.id,)})
    )
    w.repo.reserve_digest_events(identities[0], (earlier.id,))
    w.repo.mark_publication(identities[0], PublicationStatus.SENT, message_id=777)
    w.repo.complete_digest_events(identities[0], message_id=777, sent_at=w.clock.now())
    w.repo.mark_publication(identities[1], PublicationStatus.UNKNOWN, count_attempt=False)
    w.command(f"/delivery {identities[1]} dismiss checked absence")

    w.run(discover=False, analyze=False, track=False)
    w.repo.restore(w.repo.dump())

    sent = w.repo.publication_by_id(earlier.id)
    dismissed = w.repo.publication_by_id(later.id)
    assert sent is not None and sent.status is PublicationStatus.SENT and sent.message_id == 777
    assert dismissed is not None and dismissed.status is PublicationStatus.DISMISSED
    assert w.repo.digest_owner(earlier.id) == identities[0]
    assert w.repo.digest_owner(later.id) == identities[-1]
    assert not w.repo.delivery_resolutions(identities[0])
    assert w.repo.delivery_resolutions(identities[-1])
