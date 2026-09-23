"""The act in the Senate: the card names the committees that took it, their e-mail and their day."""

import datetime as dt

from lexinform.models import SenateAct, SenateCommittee, Stage
from tests.harness import COMMITTEE_STAGES, World

ACT_URL = "https://www.senat.gov.pl/prace/proces-legislacyjny-w-senacie/ustawa,2169.html"


PASSED = (
    *COMMITTEE_STAGES,
    Stage(
        stage_name="III czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        date=dt.date(2026, 9, 4),
        decision="uchwalono",
    ),
    Stage(stage_name="Uchwalono", stage_type="End"),
)


def _passed() -> World:
    """A followed bill the Sejm passed on 2026-09-04, seen on 2026-09-07."""
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.run()
    w.set_stages("3039", PASSED)
    w.touch("3039", dt.datetime(2026, 9, 6, 9, tzinfo=dt.UTC), passed=True)
    w.run()
    return w


def _act(**overrides: object) -> SenateAct:
    fields: dict[str, object] = dict(
        url=ACT_URL,
        title="Ustawa o cudzoziemcach",
        print_number="849",
        received=dt.date(2026, 9, 5),
        committees=(
            SenateCommittee(
                id=228, name="Komisja Praw Człowieka i Praworządności", email="kpcp@senat.gov.pl"
            ),
        ),
    )
    return SenateAct.model_validate(fields | overrides)


def _card(w: World) -> str:
    message, _ = w.publisher.edited[-1]
    return message.text


def test_an_act_the_senate_has_not_listed_yet_promises_the_committee_by_name() -> None:
    w = _passed()

    card = _card(w)

    assert "Сенат назначит её в ближайшие дни" in card
    assert "профильную комиссию" not in card
    assert w.bill("3039").senate is None


def test_the_card_names_the_committee_its_email_and_the_day_it_meets() -> None:
    w = _passed()
    w.senate.put("o cudzoziemcach", _act(committee_sittings=(dt.date(2026, 9, 9),)))

    w.run()

    card = _card(w)
    assert (
        'направить мнение в комиссию Сената — <a href="https://www.senat.gov.pl/prace/'
        'komisje-senackie/komisja,228.html">Komisja Praw Człowieka i Praworządności</a>'
        " (kpcp@senat.gov.pl) до заседания 09.09.2026"
        " (на польском, с номером сенатского druk nr 849)"
        f' · <a href="{ACT_URL}">закон на сайте Сената</a>'
    ) in card
    assert w.bill("3039").senate == _act(committee_sittings=(dt.date(2026, 9, 9),))


def test_the_stored_page_is_read_again_rather_than_looked_up() -> None:
    w = _passed()
    w.senate.put("o cudzoziemcach", _act())
    w.run()
    w.senate.calls.clear()

    w.run()

    assert w.senate.calls == [f"read {ACT_URL}"]


def test_once_the_committees_have_met_the_card_invites_nothing_more() -> None:
    w = _passed()
    w.senate.put("o cudzoziemcach", _act(committee_sittings=(dt.date(2026, 9, 8),)))
    w.clock.advance(days=2)  # 2026-09-09

    w.run()

    card = _card(w)
    assert "комиссии Сената уже рассмотрели закон, дальше голосование Сената" in card
    assert "kpcp@senat.gov.pl" not in card


def test_an_older_act_of_the_same_title_is_not_taken_for_this_one() -> None:
    w = _passed()  # passed on 2026-09-04
    w.senate.put("o cudzoziemcach", _act(received=dt.date(2026, 6, 1)))

    w.run()

    assert w.bill("3039").senate is None


def test_the_senate_being_down_stops_only_its_own_part() -> None:
    w = _passed()
    w.senate.outage = True

    report = w.run()

    assert any(e.startswith("tracking: senate: ") for e in report.errors)
    assert "Сенат назначит её в ближайшие дни" in _card(w)
