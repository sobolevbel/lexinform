import datetime as dt
from typing import Literal

import pytest

from lexinform.delivery_schedule import daily_schedule
from lexinform.errors import DeliveryUncertainError
from lexinform.models import (
    AttentionAssessment,
    Bill,
    BillSubmission,
    Editorial,
    Mnemonic,
    PriorityOverride,
    PublicationKind,
    PublicationStatus,
    RunReport,
    Stage,
    StatusChange,
    participation_view,
)
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, COMMITTEE_STAGES, RCL, TERM, World
from tests.scenario.test_tracking_agenda import ASW, _sitting


def save_window(w: World, end: dt.date | None, *, submission_number: str = "RPW/1/2026") -> None:
    w.repo.save_submission(
        TERM,
        "3039",
        BillSubmission(
            term=TERM,
            number=submission_number,
            title="Учебный проект",
            date_of_receipt=w.clock.now().date(),
            public_consultation=True,
            consultation_end=end,
        ),
    )


def test_shortened_window_releases_routine_queue_without_rebasing_the_event() -> None:
    w = World(reader_attention="on", attention_roundup="daily")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    save_window(w, w.clock.now().date() + dt.timedelta(days=10))
    w.set_stages("3039", COMMITTEE_STAGES)
    assert w.run().updates == 0
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None
    frozen_event = pub.delivery.bill_json
    w.clock.advance(hours=1)
    end = w.clock.now().date() + dt.timedelta(days=2)
    save_window(w, end)
    calls = list(w.gateway.calls)

    report = w.container.delivery_service(dry_run=False).run()

    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert report.ok and report.updates == 1 and report.attention["urgent"] == 1
    assert w.gateway.calls == calls
    assert pub is not None and pub.delivery is not None and pub.attention is not None
    assert pub.delivery.bill_json == frozen_event and pub.attention.deadline == end
    assert pub.delivery.participation is not None
    assert w.formatter.fmt_date(end) in pub.delivery.rendered_text
    original = Bill.model_validate_json(frozen_event)
    assert original.consultation is not None and original.consultation.end != end


def test_reminder_refreshes_date_and_route_even_when_priority_does_not_change() -> None:
    w = World(reader_attention="on", attention_roundup="daily")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    save_window(w, w.clock.now().date() + dt.timedelta(days=2))
    pub = w.container.poster(dry_run=False).prepare_message(
        w.bill("3039"), PublicationKind.CONSULTATION_DEADLINE, today=w.clock.now().date()
    )
    assert pub.delivery is not None
    original = pub.delivery.bill_json
    end = w.clock.now().date() + dt.timedelta(days=1)
    save_window(w, end, submission_number="RPW/2/2026")
    w.repo.restore(w.repo.dump())

    report = w.container.delivery_service(dry_run=False).run()

    sent = w.repo.publication_by_id(pub.id or 0)
    assert report.ok and report.consultation_reminders == 1
    assert sent is not None and sent.delivery is not None and sent.attention is not None
    assert sent.delivery.bill_json == original and sent.attention.deadline == end
    assert sent.delivery.attention_history and sent.delivery.participation is not None
    assert "https://opiniowanie.sejm.gov.pl/RPW-2-2026" in sent.delivery.rendered_text
    assert w.formatter.fmt_date(end) in sent.delivery.rendered_text


def test_disappeared_window_waits_for_evidence_without_invitation_or_attempt() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    save_window(w, w.clock.now().date() + dt.timedelta(days=2))
    pub = w.container.poster(dry_run=False).prepare_message(
        w.bill("3039"), PublicationKind.CONSULTATION_DEADLINE, today=w.clock.now().date()
    )
    save_window(w, None)

    report = w.container.delivery_service(dry_run=False).run()

    waiting = w.repo.publication_by_id(pub.id or 0)
    assert report.ok and report.attention["participation_unavailable"] == 1
    assert waiting is not None and waiting.status is PublicationStatus.QUEUED
    assert waiting.attempts == 0 and not w.publisher.texts(PublicationKind.CONSULTATION_DEADLINE)
    save_window(w, w.clock.now().date() + dt.timedelta(days=1))
    resumed = w.container.delivery_service(dry_run=False).run()
    assert resumed.ok and resumed.consultation_reminders == 1


def test_rcl_reminder_refreshes_email_letter_and_day_count_without_rebasing_source() -> None:
    w = World(reader_attention="on")
    w.add_rcl_project()
    w.run(track=False)
    bill = w.bill(RCL)
    assert bill.rcl is not None and bill.rcl.consultation is not None
    pub = w.container.poster(dry_run=False).prepare_message(
        bill, PublicationKind.CONSULTATION_DEADLINE, today=w.clock.now().date()
    )
    assert pub.delivery is not None
    original = pub.delivery.bill_json
    consultation = bill.rcl.consultation.model_copy(
        update={
            "email": "konsultacje@mswia.gov.pl",
            "letter_url": "https://legislacja.rcl.gov.pl/docs/revised-letter.pdf",
            "days": 14,
        }
    )
    w.repo.save_rcl(TERM, RCL, bill.rcl.model_copy(update={"consultation": consultation}))
    calls = list(w.rcl.calls)

    report = w.container.delivery_service(dry_run=False).run()

    sent = w.repo.publication_by_id(pub.id or 0)
    assert report.ok and report.consultation_reminders == 1 and w.rcl.calls == calls
    assert sent is not None and sent.delivery is not None
    assert sent.delivery.bill_json == original and sent.delivery.participation is not None
    assert "konsultacje@mswia.gov.pl" in sent.delivery.rendered_text
    assert "dep.prawny@mswia.gov.pl" not in sent.delivery.rendered_text
    assert consultation.letter_url is not None
    assert consultation.letter_url in sent.delivery.rendered_text
    assert sent.delivery.participation.consultation is not None
    assert sent.delivery.participation.consultation.days == 14
    preview = w.formatter.new_bill(
        participation_view(bill, sent.delivery.participation), None, today=w.clock.now().date()
    )
    assert "14 дн. с даты письма" in preview.text


def test_newly_expired_consultation_is_retired_and_reported_once() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    save_window(w, w.clock.now().date() + dt.timedelta(days=2))
    pub = w.container.poster(dry_run=False).prepare_message(
        w.bill("3039"), PublicationKind.CONSULTATION_DEADLINE, today=w.clock.now().date()
    )
    save_window(w, w.clock.now().date() - dt.timedelta(days=1))
    notices = len(w.notifier.calls)

    report = w.container.delivery_service(dry_run=False).run()

    retired = w.repo.publication_by_id(pub.id or 0)
    assert report.ok and report.attention["expired"] == 1
    assert retired is not None and retired.status is PublicationStatus.SKIPPED
    assert retired.attempts == 0 and not w.publisher.texts(PublicationKind.CONSULTATION_DEADLINE)
    assert len(w.notifier.calls) == notices + 1
    w.container.delivery_service(dry_run=False).run()
    assert len(w.notifier.calls) == notices + 1


def test_changed_window_does_not_rewrite_an_attempted_reminder() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    save_window(w, w.clock.now().date() + dt.timedelta(days=2))
    w.container.poster(dry_run=False).prepare_message(
        w.bill("3039"), PublicationKind.CONSULTATION_DEADLINE, today=w.clock.now().date()
    )
    w.publisher.fail_on.add("3039")
    assert not w.container.delivery_service(dry_run=False).run().ok
    failed = w.publication("3039", PublicationKind.CONSULTATION_DEADLINE)
    assert failed is not None and failed.delivery is not None and failed.delivery.rendered_text
    frozen = failed.delivery.rendered_text
    save_window(w, w.clock.now().date() + dt.timedelta(days=1))
    w.publisher.fail_on.clear()
    w.repo.restore(w.repo.dump())

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and report.consultation_reminders == 1
    assert w.publisher.texts(PublicationKind.CONSULTATION_DEADLINE)[-1] == frozen


def queued_world(*, workers: int = 1) -> World:
    w = World(reader_attention="on", attention_roundup="daily", workers=workers)
    for number in ("3039", "3040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    report = w.run()
    assert report.ok and report.updates == 0 and report.attention["daily_queued"] == 1
    return w


def overdue_unknown_world() -> World:
    w = queued_world()
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.id is not None
    w.repo.mark_publication(
        pub.id, PublicationStatus.UNKNOWN, error="uncertain send", count_attempt=False
    )
    w.clock.advance(hours=25)
    return w


def test_overdue_alert_is_persistent_and_repeats_daily_instead_of_hourly() -> None:
    w = overdue_unknown_world()
    calls = len(w.notifier.calls)

    first = w.container.delivery_service(dry_run=False).run()

    assert first.ok and first.attention["daily_overdue"] == 1
    assert len(w.notifier.calls) == calls + 1
    notice = w.repo.get_delivery_alert(CHANNEL)
    assert notice is not None and notice.notified_at == w.clock.now()
    w.repo.restore(w.repo.dump())
    w.repo.prune_runs(before=w.clock.now() + dt.timedelta(days=1))
    w.clock.advance(hours=1)

    repeat = w.container.delivery_service(dry_run=False).run()

    assert repeat.attention["daily_overdue"] == 1
    assert len(w.notifier.calls) == calls + 1
    assert w.repo.get_delivery_alert(CHANNEL) == notice
    assert not w.publisher.texts(PublicationKind.STATUS_UPDATE)
    w.clock.advance(hours=23)
    w.container.delivery_service(dry_run=False).run()
    assert len(w.notifier.calls) == calls + 2


def test_changed_problem_triggers_an_alert_before_the_daily_repeat() -> None:
    w = overdue_unknown_world()
    w.container.delivery_service(dry_run=False).run()
    calls = len(w.notifier.calls)
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.id is not None
    w.repo.mark_publication(
        pub.id, PublicationStatus.UNKNOWN, error="changed problem", count_attempt=False
    )

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and len(w.notifier.calls) == calls + 1


def test_failed_operator_notification_does_not_suppress_the_next_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = overdue_unknown_world()

    def fail(report: RunReport, lines: list[str]) -> None:
        raise RuntimeError("log channel unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(w.notifier, "notify", fail)
        w.container.delivery_service(dry_run=False).run()
    assert w.repo.get_delivery_alert(CHANNEL) is None

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and w.repo.get_delivery_alert(CHANNEL) is not None
    assert w.notifier.calls[-1][0] == report


def test_dry_run_does_not_advance_the_delivery_alert_cursor() -> None:
    w = overdue_unknown_world()
    before = w.repo.dump()

    report = w.container.delivery_service(dry_run=True).run(dry_run=True)

    assert report.ok and w.repo.dump() == before
    assert w.repo.get_delivery_alert(CHANNEL) is None


def test_compact_roundup_preserves_case_links_dates_committees_and_tags() -> None:
    w = queued_world()
    w.set_stages("3040", COMMITTEE_STAGES)
    assert w.run().updates == 0
    w.clock.advance(hours=10)

    report = w.container.delivery_service(dry_run=False).run()

    text = w.publisher.texts(PublicationKind.STATUS_UPDATE)[-1]
    assert report.ok and (report.updates, report.update_events) == (1, 2)
    assert text.count("Что дальше:") == 1 and text.count("Что можно сделать сейчас:") == 1
    assert "Пока проект" not in text
    assert text.count("Направлен в комиссию: ASW") == 2
    assert text.count(w.formatter.fmt_date(dt.date(2026, 9, 3))) == 2
    assert "Путь проекта" not in text
    for number in ("3039", "3040"):
        assert f"#kadencja10druk{number}" in text
        assert f"https://t.me/test/{w.card_id(number)}" in text


@pytest.mark.parametrize("workers", [1, 4])
def test_accumulates_across_runs_then_drains_without_sources_or_models(workers: int) -> None:
    w = queued_world(workers=workers)
    first = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert first is not None and first.delivery is not None and first.delivery.schedule is not None
    schedule = first.delivery.schedule
    assert schedule.not_before == dt.datetime(2026, 9, 7, 16, tzinfo=dt.UTC)
    assert first.status is PublicationStatus.QUEUED and first.attempts == 0
    notifications = len(w.notifier.calls)
    idle = w.container.delivery_service(dry_run=False).run()
    assert idle.is_empty and idle.ok and len(w.notifier.calls) == notifications
    w.clock.advance(hours=5)
    w.set_stages("3040", COMMITTEE_STAGES)
    assert w.run().updates == 0
    w.repo.restore(w.repo.dump())
    w.repo.prune_history(before=w.clock.now() + dt.timedelta(days=1), memo_before=w.clock.now())
    w.clock.advance(hours=5)
    calls = list(w.gateway.calls)
    analyses = len(w.llm.contexts)

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and (report.updates, report.update_events) == (1, 2)
    assert report.attention["daily_released"] == 2
    assert report.attention["messages"] == 1 and "daily_queued" not in report.attention
    assert w.gateway.calls == calls and len(w.llm.contexts) == analyses
    for number in ("3039", "3040"):
        pub = w.publication(number, PublicationKind.STATUS_UPDATE)
        assert pub is not None and pub.status is PublicationStatus.SENT and pub.delivery is not None
        assert len(pub.delivery.update_group) == 2
    assert w.container.delivery_service(dry_run=False).run().updates == 0


def test_same_case_merges_routine_stages_without_restarting_editorial_deadline() -> None:
    w = queued_world()
    first = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert first is not None and first.delivery is not None and first.delivery.schedule is not None
    schedule = first.delivery.schedule
    w.clock.advance(hours=4)
    w.set_stages(
        "3039",
        (
            *COMMITTEE_STAGES,
            Stage(stage_type="Referral", stage_name="Do komisji", committee_code="FIN"),
        ),
    )
    report = w.run()
    pubs = w.repo.list_attention_publications(TERM, "3039", limit=20)
    owner = next(pub for pub in pubs if pub.delivery and pub.delivery.held_change_ids)
    assert owner.delivery is not None and owner.delivery.schedule == schedule
    change = StatusChange.model_validate_json(owner.delivery.change_json)
    assert any(stage.committee_code == "FIN" for stage in change.new_stages)
    assert report.ok and report.attention["daily_queued"] == 1
    w.clock.advance(hours=6)

    released = w.container.delivery_service(dry_run=False).run()

    assert released.ok and released.updates == 1
    original = w.repo.publication_by_id(first.id or 0)
    assert original is not None and original.status is PublicationStatus.SENT
    delivered = w.repo.publication_by_id(owner.id or 0)
    assert delivered is not None and original.message_id == delivered.message_id


def test_operator_priority_releases_saved_event_before_daily_window() -> None:
    w = queued_world()
    w.clock.advance(hours=1)
    w.repo.save_editorial(
        TERM,
        "3039",
        Editorial(
            priority=PriorityOverride(
                important=True,
                command_update_id=123,
                expires_at=w.clock.now() + dt.timedelta(days=1),
                reason="Подтверждён важный эффект",
                selected_at=w.clock.now(),
            )
        ),
    )

    report = w.container.delivery_service(dry_run=False).run()

    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert report.ok and report.updates == 1
    assert pub is not None and pub.status is PublicationStatus.SENT and pub.delivery is not None
    assert pub.attention is not None and "operator_priority" in pub.attention.reasons
    assert pub.delivery.attention_history and not pub.delivery.update_group


def test_delayed_runner_reports_overdue_and_releases_on_first_available_run() -> None:
    w = queued_world()
    w.clock.advance(hours=30)

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and report.updates == 1 and report.attention["daily_overdue"] == 1
    assert w.notifier.calls[-1][0] == report
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None
    assert pub.delivery.today == w.clock.now().date()


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_disabling_policy_releases_unattempted_daily_queue(mode: Literal["off", "shadow"]) -> None:
    w = queued_world()
    w.container.settings.reader_attention = mode
    publisher = w.container.publisher(dry_run=False)
    # A new container represents the next process reading the changed configuration.
    w.container = w.container.__class__(
        settings=w.container.settings,
        clock=w.clock,
        repo=w.repo,
        gateway=w.gateway,
        formatter=w.formatter,
        prefilter=w.container.prefilter,
        terms=w.container.terms,
        publisher_override=publisher,
        notifier_override=w.notifier,
    )

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and report.updates == 1


def test_sittings_wait_and_disappeared_unannounced_sitting_is_not_delivered() -> None:
    w = World(reader_attention="on", attention_roundup="daily")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.gateway.committees["ASW"] = ASW
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    assert w.run().agenda_posted == 0
    ref = w.bill("3039").agenda[0].ref
    pub = w.repo.get_publication(TERM, "3039", PublicationKind.AGENDA, CHANNEL, ref=ref)
    assert pub is not None and pub.delivery is not None and pub.delivery.schedule is not None
    w.gateway.committee_sittings["ASW"] = ()
    assert w.run().agenda_cancelled == 0
    w.clock.advance(hours=10)

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and report.agenda_posted == 0
    pub = w.repo.publication_by_id(pub.id or 0)
    assert pub is not None and pub.status is PublicationStatus.SKIPPED


def test_near_sitting_bypasses_daily_wait() -> None:
    w = World(reader_attention="on", attention_roundup="daily")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.gateway.committees["ASW"] = ASW
    w.run()
    w.gateway.committee_sittings["ASW"] = (
        _sitting(date=w.clock.now().date() + dt.timedelta(days=1)),
    )

    report = w.run()

    assert report.ok and report.agenda_posted == 1


def test_overdue_unannounced_sitting_is_retired_without_inviting_to_past_event() -> None:
    w = World(reader_attention="on", attention_roundup="daily")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.gateway.committees["ASW"] = ASW
    w.run()
    w.gateway.committee_sittings["ASW"] = (_sitting(),)
    assert w.run().agenda_posted == 0
    w.clock.advance(days=10, hours=2)

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and report.agenda_posted == 0 and report.attention["expired"] == 1


def test_important_news_releases_the_same_cases_waiting_routine_stages() -> None:
    w = queued_world()
    original = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert original is not None
    w.set_stages(
        "3039",
        (
            *COMMITTEE_STAGES,
            Stage(
                stage_type="PublicHearing",
                stage_name="Wysłuchanie publiczne",
                date=w.clock.now().date() + dt.timedelta(days=15),
            ),
        ),
    )

    report = w.run()

    assert report.ok and report.updates == 1
    pub = w.repo.publication_by_id(original.id or 0)
    assert pub is not None and pub.status is PublicationStatus.SENT
    assert "daily_queued" not in report.attention


def test_shadow_daily_setting_keeps_delivery_per_run() -> None:
    w = World(reader_attention="shadow", attention_roundup="daily")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)

    report = w.run()

    assert report.ok and report.updates == 1
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None and pub.delivery.schedule is None


def test_delivery_drain_dry_run_rolls_back_saved_queue_and_runs() -> None:
    w = queued_world()
    w.clock.advance(hours=10)
    before = w.repo.dump()

    report = w.container.delivery_service(dry_run=True).run(dry_run=True)

    assert report.ok and report.updates == 1 and w.repo.dump() == before


def test_daily_limit_splits_ready_queue_without_dropping_events() -> None:
    w = World(reader_attention="on", attention_roundup="daily")
    numbers = [str(number) for number in range(3030, 3036)]
    for number in numbers:
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    for number in numbers:
        w.set_stages(number, COMMITTEE_STAGES)
    assert w.run().updates == 0
    w.clock.advance(hours=10)

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and (report.updates, report.update_events) == (2, 6)
    assert all(
        (pub := w.publication(number, PublicationKind.STATUS_UPDATE)) is not None
        and pub.status is PublicationStatus.SENT
        for number in numbers
    )


@pytest.mark.parametrize("uncertain", [False, True])
def test_daily_group_recovery_preserves_all_members_and_html(
    uncertain: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = queued_world()
    w.set_stages("3040", COMMITTEE_STAGES)
    w.run()
    w.clock.advance(hours=10)

    def failed(text: str) -> PublishResult:
        if uncertain:
            raise DeliveryUncertainError("connection lost")
        raise RuntimeError("definite rejection")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_updates", failed)
        assert not w.container.delivery_service(dry_run=False).run().ok
    member = w.publication("3040", PublicationKind.STATUS_UPDATE)
    assert member is not None and member.delivery is not None and member.id is not None
    frozen = member.delivery.group_text
    assert member.status is (PublicationStatus.UNKNOWN if uncertain else PublicationStatus.FAILED)
    assert len(member.delivery.update_group) == 2
    w.repo.restore(w.repo.dump())
    w.clock.advance(days=1)
    w.repo.save_editorial(
        TERM,
        "3039",
        Editorial(
            mnemonic=Mnemonic(
                name="Новое имя дела", language="ru", basis="Правка", selected_at=w.clock.now()
            )
        ),
    )
    if uncertain:
        assert w.container.delivery_service(dry_run=False).run().updates == 0
        w.command(f"/delivery {member.id} retry")
        commands = w.container.command_service(dry_run=False)
        assert commands is not None
        commands.handle_pending(min_score=3, run_started_at=w.clock.now())

    report = w.container.delivery_service(dry_run=False).run()

    assert report.ok and report.updates == 1
    assert w.publisher.texts(PublicationKind.STATUS_UPDATE)[-1] == frozen


@pytest.mark.parametrize(
    ("day", "utc_hour", "wait"),
    [
        (dt.date(2026, 3, 28), 17, dt.timedelta(hours=22, minutes=59)),
        (dt.date(2026, 10, 24), 16, dt.timedelta(hours=24)),
    ],
)
def test_daily_deadline_is_elapsed_24_hours_across_dst(
    day: dt.date, utc_hour: int, wait: dt.timedelta
) -> None:
    now = dt.datetime.combine(day, dt.time(utc_hour, 1), tzinfo=dt.UTC)
    assessment = AttentionAssessment(
        importance="routine", urgency="normal", detected_at=now, assessed_at=now
    )

    schedule = daily_schedule(
        assessment, PublicationKind.STATUS_UPDATE, now=now, release_hour=18, urgent_days=3
    )

    assert schedule is not None
    assert schedule.deliver_by - now == dt.timedelta(hours=24)
    assert schedule.not_before - now == wait
