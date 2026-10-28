"""The weekly digest end to end: drafted on a Warsaw Sunday, published only on the button."""

import datetime as dt
from typing import Any

import pytest

from lexinform.container import Container
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunMode,
    RunReport,
    SejmSitting,
    Stage,
    StatusChange,
    iso_week,
)
from lexinform.services.digest import DigestService
from lexinform.services.terms import TermResolver
from lexinform.settings import Settings
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import (
    CHANNEL,
    COMMITTEE_STAGES,
    REFERRED,
    SUPPORT_URL,
    TERM,
    World,
    submission,
)

TITLE = "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach"
# The World's clock starts on Monday 2026-09-07; the digest's day is the Sunday that ends it.
TO_SUNDAY = 6


@pytest.mark.parametrize("draft", [False, True])
@pytest.mark.parametrize(
    "status", [PublicationStatus.PENDING, PublicationStatus.UNKNOWN, PublicationStatus.DISMISSED]
)
def test_digest_never_resends_uncertain_or_dismissed_delivery(
    draft: bool, status: PublicationStatus
) -> None:
    w = World()
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    send = service.draft if draft else service.publish
    ref = "2026-W36"
    send(ref)
    channel = w.container.drafts_channel_id() if draft else CHANNEL
    publication = w.repo.get_publication(
        DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, channel, ref=ref
    )
    assert publication is not None and publication.id is not None
    w.repo.mark_publication(publication.id, status, count_attempt=False)
    before = w.repo.publication_by_id(publication.id)
    messages = list(w.publisher.texts(PublicationKind.DIGEST))
    w.repo.restore(w.repo.dump())

    result = send(ref)

    assert not result.published and not result.drafted
    assert w.publisher.texts(PublicationKind.DIGEST) == messages
    assert w.repo.publication_by_id(publication.id) == before


def _sunday(w: World) -> None:
    w.clock.advance(days=TO_SUNDAY)


def _commands_only(w: World, **options: Any) -> RunReport:
    return w.run(
        mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0, **options
    )


def _drafts(w: World) -> list[str]:
    return w.publisher.texts(PublicationKind.DIGEST)


def test_no_digest_is_drafted_on_a_day_that_is_not_its_own() -> None:
    w = World()
    w.add_bill("3039", TITLE)

    report = w.run()

    assert _drafts(w) == []
    assert report.digest_drafted is False


def test_the_sunday_run_drafts_the_week_into_the_technical_channel() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    _sunday(w)

    report = w.run()

    assert report.digest_drafted is True
    (draft,) = _drafts(w)
    assert "Итоги недели" in draft
    assert "druk 3039" in draft  # the card of the week, named and linked
    assert "https://t.me/test/101" in draft


def test_weekly_figures_include_the_current_run_and_survive_rebuilding() -> None:
    w = World(
        extractor=FakeTextExtractor("Art. 1. Podatek. " * 100),
        llm_script={"4102": make_analysis(score=2)},
    )
    _sunday(w)
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")
    w.add_bill("4101", TITLE)
    w.add_bill("4102", TITLE)
    w.add_bill("4103", "Rządowy projekt ustawy o lasach", with_pdf=False)

    w.run()

    (draft,) = _drafts(w)
    expected = ("новых проектов просмотрено — 4", "взято в канал — 1", "на контроле — 1")
    assert all(line in draft for line in expected)
    w.command(f"/digest publish ref={iso_week(w.clock.now().date())}")
    _commands_only(w)
    assert all(line in _drafts(w)[-1] for line in expected)


def test_weekly_figures_use_warsaw_week_boundaries() -> None:
    w = World()
    starts = dt.datetime(2026, 9, 6, 22, tzinfo=dt.UTC)
    for when, count in (
        (starts - dt.timedelta(seconds=1), 100),
        (starts, 2),
        (starts + dt.timedelta(days=7) - dt.timedelta(seconds=1), 3),
        (starts + dt.timedelta(days=7), 200),
    ):
        report = RunReport(
            started_at=when,
            finished_at=when,
            since=when,
            mode=RunMode.RUN,
            discovered=count,
            prefilter_rejected=[],
        )
        w.repo.finish_run(w.repo.start_run(report), report)
    service = w.container.digest_service(dry_run=False)
    assert service is not None

    week = service.build("2026-W37")

    assert week.figures is not None
    assert week.figures.discovered == 5
    assert week.figures.filtered == 0


def test_cli_read_rows_neither_complete_daily_checks_nor_change_weekly_figures() -> None:
    w = World()
    service = w.container.digest_service(dry_run=False)
    assert service is not None, "The World configures a digest publisher"
    report = RunReport(
        started_at=w.clock.now(),
        since=w.clock.now(),
        mode=RunMode.CLI_READS,
        cli_read_operation="scan",
        discovered=999,
        errors=["document unavailable"],
    )
    w.repo.finish_run(w.repo.start_run(report), report)

    alone = service.build("2026-W37")
    full = RunReport(
        started_at=w.clock.now(),
        since=w.clock.now(),
        mode=RunMode.RUN,
        discovered=3,
        prefilter_rejected=[],
    )
    w.repo.finish_run(w.repo.start_run(full), full)
    together = service.build("2026-W37")

    assert alone.checks_incomplete and alone.figures is None
    assert not together.checks_incomplete
    assert together.figures is not None, "The weekly full execution supplies figures"
    assert together.figures.discovered == 3 and together.figures.filtered == 0


def test_the_draft_carries_the_button_and_the_channel_has_nothing_yet() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    _sunday(w)

    w.run()

    sent = [m for m in w.publisher.sent if m.kind is PublicationKind.DIGEST]
    assert len(sent) == 1
    assert sent[0].action == (
        "📣 Publish to the channel",
        f"digest:{iso_week(w.clock.now().date())}",
    )
    assert (
        w.repo.get_publication(DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, CHANNEL) is None
    )


def test_the_week_is_drafted_once_however_many_runs_the_sunday_has() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    _sunday(w)

    first = w.run()
    second = w.run()

    assert (first.digest_drafted, second.digest_drafted) == (True, False)
    assert len(_drafts(w)) == 1


def test_the_button_publishes_the_week_to_the_readers_channel() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    _sunday(w)
    w.run()
    ref = iso_week(w.clock.now().date())
    w.command(f"/digest publish ref={ref}")

    report = _commands_only(w)

    assert report.commands == [
        f"/digest publish ref={ref} → digest (message 103): {ref} posted to the channel"
    ]
    published = w.repo.get_publication(
        DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, CHANNEL, ref=ref
    )
    assert published is not None and published.message_id is not None
    assert len(_drafts(w)) == 2  # the draft, and the copy the readers got


def test_manual_digest_repeats_drafts_even_after_publication() -> None:
    w = World()
    ref = "2026-W36"
    for update_id, command in enumerate(
        (
            f"/digest ref={ref}",
            f"/digest publish ref={ref}",
            f"/digest ref={ref}",
            f"/digest ref={ref}",
        ),
        start=1,
    ):
        w.command(command, update_id=update_id)
        report = _commands_only(w)
        assert report.commands_failed == 0

    drafts = w.repo.list_publications_between(
        w.container.drafts_channel_id(),
        since=w.clock.now() - dt.timedelta(days=1),
        until=w.clock.now() + dt.timedelta(days=1),
    )
    drafts = [p for p in drafts if p.kind is PublicationKind.DIGEST]
    assert len(drafts) == 3
    assert len({p.id for p in drafts}) == 3
    assert len({p.message_id for p in drafts}) == 3
    assert len(_drafts(w)) == 4


@pytest.mark.parametrize(
    "status", [PublicationStatus.PENDING, PublicationStatus.UNKNOWN, PublicationStatus.DISMISSED]
)
def test_repeated_draft_cannot_bypass_unresolved_delivery(status: PublicationStatus) -> None:
    w = World()
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    ref = "2026-W36"
    service.draft(ref)
    service.draft(ref)
    publication = w.repo.get_publication(
        DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, w.container.drafts_channel_id()
    )
    assert publication is not None and publication.id is not None
    w.repo.mark_publication(publication.id, status, count_attempt=False)

    result = service.draft(ref)

    assert result.failed
    assert len(_drafts(w)) == 2


def test_the_same_week_is_never_published_twice() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    _sunday(w)
    w.run()
    ref = iso_week(w.clock.now().date())
    w.command(f"/digest publish ref={ref}", update_id=1)
    _commands_only(w)
    w.command(f"/digest publish ref={ref}", update_id=2)

    report = _commands_only(w)

    assert report.commands == [
        f"/digest publish ref={ref} → digest (message 103): this week is in the channel already"
    ]
    assert len(_drafts(w)) == 2


def test_a_quiet_week_is_still_told() -> None:
    w = World()
    _sunday(w)

    w.run()

    (draft,) = _drafts(w)
    assert "Важных новостей на этой неделе не было." in draft


def test_the_digest_names_an_update_by_what_it_said() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.touch("3039", w.clock.now())
    w.run()
    _sunday(w)

    w.run()

    (draft,) = _drafts(w)
    assert "— Направлен в комиссию" in draft  # the week's news of the bill, its card included
    assert draft.count("druk 3039") == 1


REJECTED = (
    *REFERRED,
    Stage(
        stage_name="I czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        date=dt.date(2026, 9, 8),
        decision="odrzucono",
    ),
)


def _sent_text_update(w: World, number: str, *, rejected: bool) -> None:
    """A «new text» reply as one sent before B106 stopped them for a road that ended."""
    bill = w.bill(number)
    if rejected:
        closed = bill.summary.model_copy(update={"closure_date": dt.date(2026, 9, 8)})
        bill = bill.model_copy(update={"summary": closed, "stages": REJECTED})
    change = StatusChange(
        term=TERM,
        number=number,
        old_fingerprint="old",
        new_fingerprint=f"text/{number}",
        new_stages=[],
        content_changed=True,
        detected_at=w.clock.now(),
    )
    change_id = w.repo.add_status_change(change)
    assert change_id is not None, "the fingerprint is new for this bill"
    w.repo.create_publication(
        Publication(
            term=TERM,
            number=number,
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.SENT,
            channel_id=CHANNEL,
            status_change_id=change_id,
            message_id=200 + int(number[-1]),
            created_at=w.clock.now(),
            sent_at=w.clock.now(),
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(),
                change_json=change.model_dump_json(),
                reply_to=w.card_id(number),
            ),
        )
    )


def test_a_new_text_of_a_bill_whose_road_ended_without_a_law_is_left_out() -> None:
    """Druk 2111: «Новая версия текста» over a law the veto had killed headed draft W42."""
    w = World()
    w.add_bill("3039", TITLE)
    w.add_bill("3041", TITLE)
    w.run()
    _sent_text_update(w, "3039", rejected=True)
    _sent_text_update(w, "3041", rejected=False)
    service = w.container.digest_service(dry_run=True)
    assert service is not None, "World has a technical channel"

    week = service.build(iso_week(w.clock.now().date()))

    texts = [entry.number for entry in week.updates if entry.event == "text_changed"]
    assert texts == ["3041"]


def test_digest_groups_all_upcoming_sittings_and_omits_unfollowed_bills() -> None:
    w = World(llm_script={"4100": make_analysis(score=2)})
    w.add_bill("3039", TITLE, stages=REFERRED)
    w.add_bill("3100", "Projekt ustawy o obywatelstwie", stages=REFERRED)
    w.add_bill("4100", TITLE, stages=REFERRED)
    w.gateway.sittings = [
        SejmSitting(
            number=65,
            dates=(dt.date(2026, 9, 15), dt.date(2026, 9, 18)),
            agenda="Pierwsze czytanie (druki nr 3039, 3100, 4100)",
        ),
        SejmSitting(
            number=66,
            dates=(dt.date(2026, 9, 20),),
            agenda="Drugie czytanie (druk nr 3039)",
        ),
        SejmSitting(
            number=67,
            dates=(dt.date(2026, 9, 21),),
            agenda="Pierwsze czytanie (druk nr 4100)",
        ),
        SejmSitting(
            number=68,
            dates=(dt.date(2026, 10, 1),),
            agenda="Drugie czytanie (druk nr 3039)",
        ),
    ]
    w.run()
    _sunday(w)

    w.run()

    (draft,) = _drafts(w)
    ahead = draft.split("Заседания</b>\n")[1]
    assert ahead.count("заседание Сейма № 65") == 1
    assert ahead.count("заседание Сейма № 66") == 1
    assert "заседание Сейма № 67" not in ahead
    assert "заседание Сейма № 68" not in ahead
    assert "druk 4100" not in ahead
    assert ahead.count(f'<a href="https://t.me/test/{w.card_id("3039")}">') == 2
    assert ahead.count("· druk 3039</a>") == 2
    assert f'<a href="https://t.me/test/{w.card_id("3100")}">' in ahead
    assert "· druk 3100</a>" in ahead


def test_an_inherited_card_appears_once_in_the_digest_not_under_two_numbers() -> None:
    """`Linker._inherit_card` aliases a `new_bill` row onto the same Telegram message the
    pre-print entry's card already used — one post, not two — so a project that gets its druk the
    same week its card was sent must not be listed twice under two numbers, the RPW entry and the
    druk it became. The status update naming the new druk number and the still-open consultation
    both legitimately mention "druk 3100" too, in their own sections, so the card count is checked
    directly on `Digest.cards` rather than by scraping the rendered text for the number."""
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    w.gateway.submissions[0] = submission(print_number="3100")
    w.add_bill("3100", "Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony")
    w.clock.advance(days=1)
    w.run()

    ref = iso_week(w.clock.now().date())
    service = w.container.digest_service(dry_run=False)
    assert service is not None, "World enables the digest and names a technical channel"
    digest = service.build(ref)

    assert [c.number for c in digest.cards] == [
        "3100"
    ]  # not ["3100", RPW/...] or [RPW/..., "3100"]


def test_the_first_digest_of_a_month_carries_its_figures_and_the_ask() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    # 2026-10-04 is a Sunday whose week is the first of October, so it reports September whole.
    w.clock.advance(days=27)

    w.run()

    (draft,) = _drafts(w)
    assert "За сентябрь 2026: новых проектов просмотрено" in draft
    assert "отобрано по ключевым словам" in draft
    assert SUPPORT_URL in draft


def test_a_week_without_a_month_behind_it_carries_no_figures() -> None:
    w = World()
    _sunday(w)

    w.run()

    (draft,) = _drafts(w)
    assert "За сентябрь" not in draft and SUPPORT_URL not in draft


def test_without_a_technical_channel_there_is_no_digest_at_all() -> None:
    """The draft must never land in the readers' channel: `telegram_publisher("")` aims there,
    so "no log channel" has to mean "no digest", not "draft to whoever is left"."""
    w = World()
    real = Container(
        settings=Settings(
            _env_file=None,
            telegram_bot_token="TOKEN",
            telegram_channel_id="-100999",
            telegram_log_channel_id="",
        ),
        clock=w.clock,
        repo=w.repo,
        gateway=w.gateway,
        formatter=w.formatter,
        prefilter=KeywordPrefilter(),
        terms=TermResolver(w.gateway, w.repo),
    )

    assert real.digest_service(dry_run=False) is None


def test_a_draft_that_telegram_refuses_is_reported_as_failed_not_as_sent() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    _sunday(w)
    w.publisher.fail_on = {DIGEST_NUMBER}

    report = w.run()

    assert report.digest_drafted is False
    assert any("digest" in e and "not posted" in e for e in report.errors)
    assert _drafts(w) == []


def test_a_digest_the_command_could_not_post_says_so() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    _sunday(w)
    ref = iso_week(w.clock.now().date())
    w.publisher.fail_on = {DIGEST_NUMBER}
    w.command(f"/digest publish ref={ref}")

    report = _commands_only(w)

    assert report.commands == [
        f"/digest publish ref={ref} → error: {ref} was not posted, see the log"
    ]
    assert report.commands_failed == 1


def test_the_week_asked_for_is_the_last_one_that_ended_whatever_day_the_digest_goes_out() -> None:
    """An ISO week ends on a Sunday whatever `digest_weekday` says; a Monday digest was drafting
    the week that had just begun."""
    w = World()
    service = DigestService(
        w.repo,
        w.publisher,
        w.publisher,
        w.clock,
        channel_id=CHANNEL,
        draft_channel_id="console",
        approve_label="publish",
        weekday=0,  # Monday, which the settings allow
    )

    assert w.clock.now().weekday() == 0  # the World starts on a Monday
    assert service.current_ref() == "2026-W36"  # the week that ended yesterday, not today's


def test_the_service_answers_for_the_week_that_has_just_ended() -> None:
    w = World()
    service = w.container.digest_service(dry_run=False)
    assert isinstance(service, DigestService)

    monday = service.current_ref()
    _sunday(w)
    sunday = service.current_ref()

    assert monday == "2026-W36"  # a Monday asks about the week before it
    assert sunday == "2026-W37"  # the Sunday's own week, which ends that day
