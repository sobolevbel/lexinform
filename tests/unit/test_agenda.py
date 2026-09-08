"""Agenda HTML -> items and print numbers (pure functions on real API fixtures)."""

import json

from lexinform.agenda import agenda_items, html_to_text, items_mentioning, print_numbers
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
