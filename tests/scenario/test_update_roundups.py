from datetime import timedelta

import pytest

from lexinform.adapters.telegram_format import length
from lexinform.errors import DeliveryUncertainError, TelegramUnavailableError
from lexinform.models import (
    Bill,
    BillStatus,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
)
from lexinform.ports import PublishResult
from lexinform.services.digest import DigestService
from tests.fakes import FakeTextExtractor
from tests.formatting import assert_telegram_html
from tests.harness import (
    CHANNEL,
    COMMITTEE_STAGES,
    RCL,
    TERM,
    World,
    rcl_document,
    rcl_folder,
    rcl_stage,
)
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_tracking import REPORT_TEXT, REPORT_URL, WITH_REPORT


def two_bills(*, workers: int = 1) -> World:
    w = World(workers=workers)
    for number in ("3039", "3040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    for number in ("3039", "3040"):
        w.set_stages(number, COMMITTEE_STAGES)
    return w


@pytest.mark.parametrize("workers", [1, 4])
def test_routine_events_share_one_post_and_keep_both_identities(workers: int) -> None:
    w = two_bills(workers=workers)

    report = w.run()
    repeated = w.run()

    assert (report.updates, report.update_events, repeated.updates) == (1, 2, 0)
    assert not w.publisher.updates and len(w.publisher.update_roundups) == 1
    first = w.publication("3039", PublicationKind.STATUS_UPDATE)
    second = w.publication("3040", PublicationKind.STATUS_UPDATE)
    assert first is not None and second is not None
    assert first.id != second.id and first.message_id == second.message_id
    assert first.status is second.status is PublicationStatus.SENT
    assert first.delivery is not None and second.delivery is not None
    assert first.delivery.update_group == second.delivery.update_group
    text = w.publisher.update_roundups[0]
    assert "3039" in text and "3040" in text
    assert text.count("Что дальше:") == 1 and text.count("Что можно сделать сейчас:") == 1
    assert w.formatter.delivery_preview(first) == text
    assert length(text) <= 4096
    assert_telegram_html(text)


def test_failed_roundup_retries_the_whole_saved_payload_once_after_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = two_bills()

    def fail(text: str) -> PublishResult:
        raise RuntimeError("definite refusal")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_updates", fail)
        failed = w.run()
    first = w.publication("3039", PublicationKind.STATUS_UPDATE)
    second = w.publication("3040", PublicationKind.STATUS_UPDATE)
    assert first is not None and second is not None and first.delivery is not None
    saved = first.delivery.group_text
    analysis = w.bill("3039").analysis
    assert analysis is not None
    w.repo.save_analysis(
        TERM,
        "3039",
        analysis.model_copy(
            update={"analysis": analysis.analysis.model_copy(update={"summary": "Newer facts"})}
        ),
    )
    w.repo.restore(w.repo.dump())

    retried = w.run()
    repeated = w.run()

    assert failed.errors and first.attempts == second.attempts == 1
    assert (retried.updates, retried.update_events, repeated.updates) == (1, 2, 0)
    assert w.publisher.update_roundups == [saved]
    assert "Newer facts" not in saved


@pytest.mark.parametrize("action", ["confirm 777", "dismiss checked the channel", "retry"])
def test_operator_resolves_unknown_roundup_from_any_member_atomically(
    action: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = two_bills()

    def uncertain(text: str) -> PublishResult:
        raise DeliveryUncertainError("response lost")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_updates", uncertain)
        w.run()
    first = w.publication("3039", PublicationKind.STATUS_UPDATE)
    second = w.publication("3040", PublicationKind.STATUS_UPDATE)
    assert (
        first is not None and second is not None and first.id is not None and second.id is not None
    )
    assert first.status is second.status is PublicationStatus.UNKNOWN
    w.repo.restore(w.repo.dump())
    w.run()
    assert not w.publisher.update_roundups
    w.command(f"/delivery {second.id} {action}")

    _commands_only(w)
    report = w.run()
    w.run()

    resolved = [w.repo.publication_by_id(identity) for identity in (first.id, second.id)]
    expected = (
        PublicationStatus.DISMISSED if action.startswith("dismiss") else PublicationStatus.SENT
    )
    assert all(pub is not None and pub.status is expected for pub in resolved)
    assert (
        len(w.repo.delivery_resolutions(first.id))
        == len(w.repo.delivery_resolutions(second.id))
        == 1
    )
    assert len(w.publisher.update_roundups) == int(action == "retry")
    assert report.updates == int(action == "retry")
    if action.startswith("confirm"):
        assert all(pub is not None and pub.message_id == 777 for pub in resolved)


@pytest.mark.parametrize("accepted", [False, True])
def test_crash_around_shared_send_never_automatically_resends(
    accepted: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = two_bills()
    send = w.publisher.publish_updates

    def crash(text: str) -> PublishResult:
        if accepted:
            send(text)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_updates", crash)
        with pytest.raises(KeyboardInterrupt):
            w.run()
    w.repo.restore(w.repo.dump())

    w.run()
    w.run()

    assert len(w.publisher.update_roundups) == int(accepted)
    for number in ("3039", "3040"):
        pub = w.publication(number, PublicationKind.STATUS_UPDATE)
        assert pub is not None and pub.status is PublicationStatus.UNKNOWN


def test_outage_does_not_consume_group_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    w = two_bills()

    def outage(text: str) -> PublishResult:
        raise TelegramUnavailableError("connection refused")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_updates", outage)
        w.run()

    for number in ("3039", "3040"):
        pub = w.publication(number, PublicationKind.STATUS_UPDATE)
        assert pub is not None and pub.status is PublicationStatus.FAILED and pub.attempts == 0
    assert w.run().updates == 1


def waiting_bill(*, workers: int = 1) -> World:
    w = World(
        batch=True,
        workers=workers,
        extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.run()
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(hours=1)
    return w


@pytest.mark.parametrize("workers", [1, 4])
def test_fast_batch_and_stage_make_one_update_after_restart(workers: int) -> None:
    w = waiting_bill(workers=workers)
    waiting = w.run()
    deferred = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert deferred is not None and deferred.delivery is not None
    deadline = deferred.delivery.wait_until
    assert deadline == w.clock.now() + timedelta(hours=1)
    assert deferred.status is PublicationStatus.QUEUED and waiting.updates == 0
    w.repo.restore(w.repo.dump())
    w.clock.advance(minutes=30)
    w.batch.resolve()

    ready = w.run()
    repeated = w.run()

    assert (ready.updates, ready.reanalyzed, repeated.updates) == (1, 1, 0)
    assert len(w.publisher.updates) == 1
    bill, change, reply = w.publisher.updates[0]
    assert change.content_changed and any(
        stage.stage_type == "CommitteeReport" for stage in change.new_stages
    )
    assert reply == w.card_id("3039") and bill.analysis is not None
    assert deferred.id is not None
    released = w.repo.publication_by_id(deferred.id)
    assert released is not None and released.status is PublicationStatus.SENT
    delivered = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert delivered is not None and released.message_id == delivered.message_id


def test_batch_wait_has_a_fixed_deadline_and_no_change_still_delivers_stage() -> None:
    w = waiting_bill()
    w.run()
    first = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert first is not None and first.delivery is not None
    w.clock.advance(minutes=30)
    w.run()
    same = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert same is not None and same.delivery is not None
    assert same.delivery.wait_until == first.delivery.wait_until
    w.batch.finds_no_changes = True
    w.batch.resolve()

    report = w.run()

    assert (report.reanalyzed, report.updates) == (1, 1)
    assert not w.publisher.updates[0][1].content_changed
    assert any(
        stage.stage_type == "CommitteeReport" for stage in w.publisher.updates[0][1].new_stages
    )


def test_stage_is_released_after_one_hour_without_a_batch_result() -> None:
    w = waiting_bill()
    w.run()
    w.clock.advance(minutes=59)
    assert w.run().updates == 0
    w.clock.advance(minutes=1)

    expired = w.run()
    repeated = w.run()

    assert (expired.updates, repeated.updates) == (1, 0)
    assert w.bill("3039").status is BillStatus.BATCH_PENDING
    assert not w.publisher.updates[0][1].content_changed


def test_failed_deferred_stage_remains_retryable_with_its_original_payload() -> None:
    w = waiting_bill()
    w.run()
    w.clock.advance(hours=1)
    w.publisher.fail_on.add("3039")
    w.run()
    failed = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert failed is not None and failed.delivery is not None
    assert failed.status is PublicationStatus.FAILED and failed.delivery.wait_until is None
    saved = w.formatter.delivery_preview(failed)
    w.repo.restore(w.repo.dump())
    w.publisher.fail_on.clear()

    report = w.run()

    assert report.updates == 1
    assert w.publisher.texts(PublicationKind.STATUS_UPDATE) == [saved]


def test_operator_retry_of_a_deferred_stage_never_absorbs_new_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = waiting_bill()
    w.run()
    w.clock.advance(hours=1)

    def uncertain(
        bill: Bill, change: StatusChange, reply_to: int | None, *, with_act: bool = False
    ) -> PublishResult:
        raise DeliveryUncertainError("response lost")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_status_update", uncertain)
        w.run()
    pending = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pending is not None and pending.id is not None and pending.delivery is not None
    assert pending.status is PublicationStatus.UNKNOWN and pending.delivery.wait_until is None
    saved = w.formatter.delivery_preview(pending)
    w.batch.resolve()
    w.command(f"/delivery {pending.id} retry")

    report = w.run()

    assert report.updates == 2
    assert w.publisher.texts(PublicationKind.STATUS_UPDATE)[0] == saved
    assert not w.publisher.updates[0][1].content_changed
    assert w.publisher.updates[1][1].content_changed


def test_hearing_is_never_held_for_text_analysis() -> None:
    w = waiting_bill()
    hearing = Stage(
        stage_type="PublicHearing",
        stage_name="Wysłuchanie publiczne",
        date=w.clock.now().date() + timedelta(days=20),
    )
    w.set_stages("3039", (*WITH_REPORT, hearing))

    report = w.run()

    assert report.updates == 1 and w.publisher.updates
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None and pub.delivery.wait_until is None


def test_rcl_stage_and_text_merge_without_a_second_model_call() -> None:
    w = World(batch=True, extractor=FakeTextExtractor(by_content={b"%PDF-revision": REPORT_TEXT}))
    project = w.add_rcl_project()
    w.run()
    w.batch.resolve()
    w.run()
    w.clock.advance(days=14)
    doc = rcl_document(999991, "projekt_ustawy.zip", created=w.clock.now().date())
    stage = rcl_stage(
        9,
        "Stały Komitet Rady Ministrów",
        "active",
        rcl_folder(999990, "Projekt ustawy", doc),
        modified=w.clock.now().date(),
    )
    w.add_rcl_project(
        project.model_copy(
            update={"stages": (*project.stages[:4], stage), "modified": w.clock.now().date()}
        )
    )
    w.rcl.files[doc.url] = b"%PDF-revision"
    before = len(w.batch.submitted)

    waiting = w.run()
    w.clock.advance(minutes=30)
    w.batch.resolve()
    collected = w.run()
    repeated = w.run()

    assert waiting.updates == 0
    assert (collected.updates, collected.reanalyzed, repeated.updates) == (1, 1, 0)
    assert len(w.batch.submitted) == before + 1
    assert w.publisher.updates[0][0].number == RCL
    change = w.publisher.updates[0][1]
    assert change.content_changed and any(
        "Stały Komitet" in s.stage_name for s in change.new_stages
    )
    text = w.publisher.texts(PublicationKind.STATUS_UPDATE)[0]
    assert "Текст проекта (ZIP)" in text


def test_digest_keeps_each_bill_in_a_shared_post() -> None:
    w = two_bills()
    w.run()

    service = DigestService(
        w.repo,
        w.publisher,
        None,
        w.clock,
        channel_id=CHANNEL,
        draft_channel_id="",
        approve_label="Publish",
    )
    digest = service.build("2026-W37")

    assert {entry.number for entry in digest.updates} == {"3039", "3040"}
    assert len({entry.message_id for entry in digest.updates}) == 1


def test_historical_digest_keeps_group_members_after_payload_retention() -> None:
    w = two_bills()
    w.run()
    w.clock.advance(days=60)
    w.repo.prune_history(before=w.clock.now(), memo_before=w.clock.now())
    w.repo.restore(w.repo.dump())
    service = DigestService(
        w.repo,
        w.publisher,
        None,
        w.clock,
        channel_id=CHANNEL,
        draft_channel_id="",
        approve_label="Publish",
    )

    digest = service.build("2026-W37")

    assert {entry.number for entry in digest.updates} == {"3039", "3040"}
    assert len({entry.message_id for entry in digest.updates}) == 1


def test_split_preserves_every_stage_and_respects_utf16() -> None:
    w = two_bills()
    for number in ("3039", "3040"):
        committee_report = Stage(
            stage_type="CommitteeReport",
            stage_name="Sprawozdanie komisji",
            print_number=f"9{number}",
            proposal="uwagi " + "😀" * 950,
        )
        w.set_stages(number, (*COMMITTEE_STAGES, committee_report))

    report = w.run()

    assert report.updates == report.update_events == 2
    assert len(w.publisher.updates) == 2 and not w.publisher.update_roundups
    texts = w.publisher.texts(PublicationKind.STATUS_UPDATE)
    assert all(length(text) <= 4096 for text in texts)
    assert all(text.count("😀") == 950 for text in texts)


def test_independent_topics_and_report_links_survive_the_roundup() -> None:
    w = two_bills()
    for number, topic in (("3039", "Редкие болезни"), ("3040", "Права пациентов")):
        analysis = w.bill(number).analysis
        assert analysis is not None
        w.repo.save_analysis(
            TERM,
            number,
            analysis.model_copy(
                update={"analysis": analysis.analysis.model_copy(update={"summary": topic + "."})}
            ),
        )
        committee_report = Stage(
            stage_type="CommitteeReport",
            stage_name="Sprawozdanie komisji",
            print_number=f"9{number}",
            report_file=f"https://sejm.test/{number}-report.pdf",
            proposal="załączony projekt ustawy",
        )
        w.gateway.files[committee_report.report_file or ""] = b"%PDF"
        w.set_stages(number, (*COMMITTEE_STAGES, committee_report))

    report = w.run()

    assert report.updates == 1
    text = w.publisher.update_roundups[0]
    assert "Редкие болезни" in text and "Права пациентов" in text
    assert "3039-report.pdf" in text and "3040-report.pdf" in text
    assert f"https://t.me/test/{w.card_id('3039')}" in text
    assert f"https://t.me/test/{w.card_id('3040')}" in text


def test_an_unknown_group_with_a_missing_member_cannot_be_partially_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = two_bills()

    def uncertain(text: str) -> PublishResult:
        raise DeliveryUncertainError("response lost")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_updates", uncertain)
        w.run()
    first = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert first is not None and first.id is not None
    w.repo.delete_publication(TERM, "3040", PublicationKind.STATUS_UPDATE, CHANNEL)
    w.command(f"/delivery {first.id} confirm 777")

    _commands_only(w)

    unchanged = w.repo.publication_by_id(first.id)
    assert unchanged is not None and unchanged.status is PublicationStatus.UNKNOWN
    assert not w.repo.delivery_resolutions(first.id)


def test_dismissing_a_combined_update_closes_its_deferred_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = waiting_bill()
    w.run()
    deferred = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert deferred is not None and deferred.id is not None
    w.batch.resolve()

    def uncertain(
        bill: Bill, change: StatusChange, reply_to: int | None, *, with_act: bool = False
    ) -> PublishResult:
        raise DeliveryUncertainError("response lost")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_status_update", uncertain)
        w.run()
    combined = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert combined is not None and combined.id is not None
    assert combined.status is PublicationStatus.UNKNOWN
    w.command(f"/delivery {combined.id} dismiss checked the channel")

    _commands_only(w)
    w.run()

    closed = w.repo.publication_by_id(deferred.id)
    assert closed is not None and closed.status is PublicationStatus.DISMISSED
    assert not w.repo.list_deferred_updates(CHANNEL) and not w.publisher.updates


def test_pilny_news_is_delivered_separately_without_waiting() -> None:
    w = two_bills()
    w.touch("3039", w.clock.now(), urgency_status="URGENT")

    report = w.run()

    assert report.updates == report.update_events == 2
    assert not w.publisher.update_roundups
    assert {bill.number for bill, _, _ in w.publisher.updates} == {"3039", "3040"}


def test_shared_resolution_history_survives_payload_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = two_bills()

    def uncertain(text: str) -> PublishResult:
        raise DeliveryUncertainError("response lost")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_updates", uncertain)
        w.run()
    first = w.publication("3039", PublicationKind.STATUS_UPDATE)
    second = w.publication("3040", PublicationKind.STATUS_UPDATE)
    assert (
        first is not None and second is not None and first.id is not None and second.id is not None
    )
    w.command(f"/delivery {second.id} confirm 777")
    _commands_only(w)
    w.clock.advance(days=60)

    w.repo.prune_history(before=w.clock.now(), memo_before=w.clock.now())
    w.repo.restore(w.repo.dump())

    old = w.repo.publication_by_id(first.id)
    assert old is not None and old.delivery is None
    audit = w.repo.delivery_resolutions(first.id)
    assert len(audit) == 1 and audit[0].publication_ids == (first.id, second.id)
