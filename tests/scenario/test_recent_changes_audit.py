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


@pytest.mark.parametrize(
    "blocked_index",
    [
        pytest.param(
            0, marks=pytest.mark.xfail(strict=True, reason="B91: digest retry cap bypass")
        ),
        pytest.param(
            1, marks=pytest.mark.xfail(strict=True, reason="B91: digest retry cap bypass")
        ),
        2,
    ],
)
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
@pytest.mark.parametrize(
    "action",
    [
        "confirm 888",
        "retry",
        pytest.param(
            "dismiss checked absence",
            marks=pytest.mark.xfail(
                strict=True, reason="B92: digest dismissal strands later parts"
            ),
        ),
    ],
)
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
    assert not any(
        part is not None and part.status is PublicationStatus.QUEUED
        for identity in identities[blocked_index + 1 :]
        for part in (w.repo.publication_by_id(identity),)
    )


@pytest.mark.parametrize(
    "last_part",
    [
        pytest.param(
            False,
            marks=pytest.mark.xfail(strict=True, reason="B93: dismissed tail leaves absorbed work"),
        ),
        True,
    ],
)
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


@pytest.mark.xfail(strict=True, reason="B94: completed multipart cards never refresh")
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
@pytest.mark.xfail(strict=True, reason="B95: new unknown continuation leaves the run healthy")
def test_unknown_continuation_is_reported_in_its_first_run(workers: int) -> None:
    w = World(reader_attention="on", workers=workers)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    analysis = make_analysis()
    analysis.summary = "Материальное изменение для старых заявлений. " * 500
    w.llm.script["3039"] = analysis
    w.publisher.unknown_continuation_attempt = 1

    report = w.run()

    root = w.publication("3039")
    assert root is not None and root.delivery is not None
    part = w.repo.publication_by_id(root.delivery.continuation_ids[0])
    assert part is not None and part.status is PublicationStatus.UNKNOWN
    assert not report.ok
