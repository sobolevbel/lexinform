"""The weekly digest end to end: drafted on a Warsaw Sunday, published only on the button."""

from typing import Any

from lexinform.models import PublicationKind, RunMode, RunReport, iso_week
from lexinform.services.digest import DigestService
from tests.harness import CHANNEL, COMMITTEE_STAGES, SUPPORT_URL, TERM, World

TITLE = "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach"
# The World's clock starts on Monday 2026-09-07; the digest's day is the Sunday that ends it.
TO_SUNDAY = 6


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
    assert w.repo.get_publication(TERM, "DIGEST", PublicationKind.DIGEST, CHANNEL) is None


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
    published = w.repo.get_publication(TERM, "DIGEST", PublicationKind.DIGEST, CHANNEL, ref=ref)
    assert published is not None and published.message_id is not None
    assert len(_drafts(w)) == 2  # the draft, and the copy the readers got


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
    assert "На этой неделе в канале не было публикаций." in draft


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
    assert "Что изменилось" in draft
    assert "Направлен в комиссию" in draft


def test_the_first_digest_of_a_month_carries_its_figures_and_the_ask() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    # 2026-10-04 is a Sunday whose week is the first of October, so it reports September whole.
    w.clock.advance(days=27)

    w.run()

    (draft,) = _drafts(w)
    assert "Итоги месяца" in draft and "09.2026" in draft
    assert "просмотрено записей" in draft and "отсеяно по ключевым словам" in draft
    assert SUPPORT_URL in draft


def test_a_week_without_a_month_behind_it_carries_no_figures() -> None:
    w = World()
    _sunday(w)

    w.run()

    (draft,) = _drafts(w)
    assert "Итоги месяца" not in draft


def test_the_service_answers_for_the_week_that_has_just_ended() -> None:
    w = World()
    service = w.container.digest_service(dry_run=False)
    assert isinstance(service, DigestService)

    monday = service.current_ref()
    _sunday(w)
    sunday = service.current_ref()

    assert monday == "2026-W36"  # a Monday asks about the week before it
    assert sunday == "2026-W37"  # the Sunday's own week, which ends that day
