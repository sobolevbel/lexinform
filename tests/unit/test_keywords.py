from __future__ import annotations

import pytest

from lexinform.keywords import KeywordPrefilter

prefilter = KeywordPrefilter()

POSITIVE = [
    "Rządowy projekt ustawy o zmianie ustawy o cudzoziemcach oraz niektórych innych ustaw",
    "Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony na terytorium RP",
    "Rządowy projekt ustawy o zmianie ustawy o pomocy obywatelom Ukrainy w związku z konfliktem",
    "Rządowy projekt ustawy o warunkach dopuszczalności powierzania pracy cudzoziemcom",
    "Rządowy projekt ustawy o zmianie niektórych ustaw w celu wyeliminowania nieprawidłowości w systemie wizowym",
    "Poselski projekt ustawy o zmianie ustawy o obywatelstwie polskim",
    "Rządowy projekt ustawy o zmianie ustawy o repatriacji",
    "Rządowy projekt ustawy o zmianie ustawy o Karcie Polaka",
    "Rządowy projekt ustawy o udziale Rzeczypospolitej Polskiej w Systemie Wjazdu/Wyjazdu",
    "Rządowy projekt ustawy o udziale Rzeczypospolitej Polskiej w systemie Eurodac",
    "Poselski projekt ustawy o czasowym zakazie wjazdu obywateli państw trzecich na terytorium RP w związku z presją migracyjną",
    "Rządowy projekt ustawy o zmianie ustawy o Straży Granicznej",
    "Obywatelski projekt ustawy w związku z realizacją polityki migracyjnej",
    "zezwolenia na pobyt czasowy i pobyt stały",
    "Rządowy projekt ustawy o zmianie ustawy o nabywaniu nieruchomości przez cudzoziemców",
    "ustawa o świadczeniu przez prawników zagranicznych pomocy prawnej",
    "azylu dla osób prześladowanych",
    "status uchodźcy",
]

# Known accepted false positive (the LLM rejects it): "ustawa o Centralnym Azylu dla Zwierząt".
NEGATIVE = [
    "Poselski projekt ustawy o zmianie ustawy o ograniczeniu handlu w niedziele i święta",
    "wizja rozwoju kraju",
    "Poselski projekt ustawy o zmianie ustawy o podatku od towarów i usług",
    "pobytu w szpitalu psychiatrycznym",
    "Rządowy projekt ustawy o zmianie ustawy - Prawo wodne",
    "telewizja publiczna",
    "granice działek budowlanych",
    "",
]


@pytest.mark.parametrize("title", POSITIVE)
def test_positive_titles_are_candidates(title: str) -> None:
    assert prefilter.match(title), title


@pytest.mark.parametrize("title", NEGATIVE)
def test_negative_titles_are_not_candidates(title: str) -> None:
    assert not prefilter.match(title), title


def test_match_uses_description_too() -> None:
    assert prefilter.match(
        "Projekt ustawy o zmianie niektórych ustaw", "projekt dotyczy cudzoziemców"
    )


def test_match_returns_pattern_names() -> None:
    assert prefilter.match("ustawa o cudzoziemcach i Karcie Polaka") == [
        "cudzoziemcy",
        "karta_polaka",
    ]
