"""Agenda HTML -> items and print numbers (pure functions on real API fixtures)."""

import datetime as dt
import json

from lexinform.agenda import (
    HearingApplication,
    SittingCondition,
    agenda_items,
    condition_covers,
    hearing_application,
    html_to_text,
    items_mentioning,
    print_numbers,
    sitting_condition,
)
from tests.conftest import FIXTURES


def _sejm_agenda() -> str:
    return str(json.loads((FIXTURES / "proceeding_65.json").read_text())["agenda"])


def _committee_agendas() -> dict[int, str]:
    data = json.loads((FIXTURES / "committee_sittings_ASW.json").read_text())
    return {int(s["num"]): str(s["agenda"]) for s in data}


def test_html_to_text_strips_tags_and_decodes_entities() -> None:
    text = html_to_text(
        '<div class="x">Rozpatrzenie &bdquo;Programu&rdquo;<br/>– przedstawia</div>'
    )
    assert text == "Rozpatrzenie „Programu”\n– przedstawia"


def test_print_numbers_handles_single_joint_and_additional_prints() -> None:
    assert print_numbers("projekt (druk nr 3035)") == {"3035"}
    assert print_numbers("(druki nr 3010 i 3055)") == {"3010", "3055"}
    assert print_numbers("druki nr 2007, 2010 i 2011") == {"2007", "2010", "2011"}
    assert print_numbers("sprawozdanie (druk nr 2689-A)") == {"2689"}
    assert print_numbers("druku nr 671 oraz 2881") == {"671", "2881"}
    assert print_numbers("bez numeru") == set()


def test_sejm_agenda_splits_into_items_with_their_prints() -> None:
    items = agenda_items(_sejm_agenda())
    assert len(items) > 30
    first = items[1]  # items[0] is the heading block
    assert first.startswith("Sprawozdanie Komisji do Spraw Deregulacji")
    assert "(druki nr 3010 i 3055)" in first and "sprawozdawca" in first
    assert items_mentioning(_sejm_agenda(), {"3055"}) == items_mentioning(_sejm_agenda(), {"3010"})
    assert items_mentioning(_sejm_agenda(), {"999999"}) == []


def test_committee_agenda_attaches_the_presenter_line_to_its_item() -> None:
    agendas = _committee_agendas()
    [item] = items_mentioning(agendas[136], {"3035"})
    assert item.startswith("Pierwsze czytanie poselskiego projektu ustawy")
    assert item.endswith("– uzasadnia poseł Marek Biernacki.")
    two = agenda_items(agendas[135])
    assert len(two) == 2 and all("przedstawia Minister" in i for i in two)
    assert items_mentioning(agendas[135], {"2831", "2832"}) == two


def test_long_items_are_clipped_at_a_word_boundary() -> None:
    long_item = "<li>Sprawozdanie (druk nr 1) " + "słowo " * 200 + "</li>"
    [clipped] = items_mentioning(long_item, {"1"})
    assert len(clipped) <= 401 and clipped.endswith("…") and not clipped.endswith(" …")


def test_a_print_named_at_the_end_of_a_long_item_survives_the_clip() -> None:
    """The Sejm names the print last, and a long title used to push it past the cut: 65 of the
    4,040 agenda items of term 10 that name a print were quoted without it."""
    item = (
        "<li>Pierwsze czytanie projektu ustawy " + "o zmianie ustawy " * 40 + "(druk nr 1486)</li>"
    )
    [clipped] = items_mentioning(item, {"1486"})
    assert len(clipped) <= 401
    assert print_numbers(clipped) == {"1486"}
    assert clipped.startswith("Pierwsze czytanie projektu") and "… " in clipped


def test_a_print_named_in_the_middle_of_a_long_item_survives_with_what_follows_it() -> None:
    item = (
        "<li>Rozpatrzenie sprawozdania "
        + "o zmianie ustawy " * 30
        + "(druk nr 232) "
        + "oraz innych ustaw " * 30
        + "</li>"
    )
    [clipped] = items_mentioning(item, {"232"})
    assert len(clipped) <= 401 and print_numbers(clipped) == {"232"}
    assert clipped.endswith("…")


def test_a_note_that_makes_the_sitting_conditional_is_told_apart_from_procedure() -> None:
    """Of the 226 committee sittings of term 10 that carry `notes`, 181 only record the procedure
    the sitting was called under and 21 say it happens only if the Sejm refers something first."""
    called = sitting_condition(
        "Posiedzenie Komisji zostało zwołane w trybie art. 152 ust. 2 regulaminu Sejmu"
    )
    whole = sitting_condition(
        "Posiedzenie aktualne w przypadku zgłoszenia poprawek w czasie drugiego czytania na"
        " posiedzeniu plenarnym Sejmu i skierowania ich do Komisji"
    )
    first_reading = sitting_condition(
        "Pkt I aktualny w przypadku zakończenia pierwszego czytania na posiedzeniu plenarnym"
        " Sejmu i skierowania projektu do Komisji"
    )
    senate = sitting_condition(
        "Posiedzenie aktualne w przypadku uchwalenia przez Senat poprawek do wymienionych ustaw"
        " oraz skierowania ich do Komisji"
    )
    range_of_points = sitting_condition(
        "Pkt. II-IV aktualne w przypadku zakończenia pierwszego czytania na posiedzeniu plenarnym"
        " Sejmu i skierowania ww. projektów ustaw do Komisji"
    )
    subcommittee = sitting_condition("Posiedzenie Podkomisji aktualne w przypadku jej powołania")

    assert called is None
    assert whole == SittingCondition("second_reading_amendments", frozenset())
    assert first_reading == SittingCondition("first_reading_referral", frozenset({1}))
    assert senate == SittingCondition("senate_amendments", frozenset())
    assert range_of_points == SittingCondition("first_reading_referral", frozenset({2, 3, 4}))
    assert subcommittee is not None and subcommittee.kind == "other"


def test_a_condition_reaches_only_the_points_it_names() -> None:
    """FPB/86 of 2024-10-17 made point III conditional and its agenda has two points, neither of
    them ours; SPC/52 made point II conditional, and point II carries no print at all. Hedging
    those two announcements would be as wrong as stating the other nineteen as settled fact."""
    numbered = (
        '<div class="agenda-indent-0">I. Pierwsze czytanie projektu (druk nr 879).</div>'
        '<div class="agenda-indent-0">II. Rozpatrzenie planu pracy Komisji.</div>'
    )
    unnumbered = (
        '<div class="agenda-indent-0">Rozpatrzenie poprawek (druki nr 2839 i 3065).</div>'
        '<div class="agenda-indent-0">Rozpatrzenie projektu (druk nr 604).</div>'
    )

    assert not condition_covers(numbered, {"879"}, frozenset({2}))
    assert condition_covers(numbered, {"879"}, frozenset({1}))
    assert condition_covers(numbered, {"879"}, frozenset())
    assert condition_covers(unnumbered, {"2839"}, frozenset({1}))
    assert not condition_covers(unnumbered, {"604"}, frozenset({1}))


def test_the_only_address_for_a_hearing_the_api_publishes_is_read_from_the_note() -> None:
    """Four sittings of term 10 carry it, all of the Rzecznik Finansowy hearing of Nov 2025, and
    the prose is the consultation letter's, which is why `rcl_letters` reads it."""
    apply = hearing_application(
        "Zgłoszenia udziału w przesłuchaniu należy przesyłać na adres e-mail:"
        " zgloszenie.RF@sejm.gov.pl w terminie do 12 listopada 2025 r."
    )

    assert apply == HearingApplication("zgloszenie.RF@sejm.gov.pl", dt.date(2025, 11, 12))
    assert hearing_application("Posiedzenie zwołane w trybie art. 152 ust. 2") is None
