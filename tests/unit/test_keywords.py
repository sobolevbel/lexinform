import pytest

from lexinform.keywords import KeywordPrefilter, accept_text_hits, accept_title_hits

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
    "Obywatelski projekt ustawy w związku z realizacją polityki migracyjnej",
    "zezwolenia na pobyt czasowy i pobyt stały",
    "Rządowy projekt ustawy o zmianie ustawy o nabywaniu nieruchomości przez cudzoziemców",
    "ustawa o świadczeniu przez prawników zagranicznych pomocy prawnej",
    "azylu dla osób prześladowanych",
    "status uchodźcy",
    # druk 1812 as the API titles it: no "cudzoziemiec", no "migracja"
    "Poselski projekt ustawy o czasowym zakazie wjazdu obywateli państw trzecich na terytorium Rzeczypospolitej Polskiej",
    "prawa wyborcze obywateli Unii Europejskiej niebędących obywatelami polskimi",
    "zasady unieważniania wiz i odmowy ich wydania",
    "zezwolenie na pracę sezonową oraz oświadczenie o powierzeniu wykonywania pracy",
    "Rządowy projekt ustawy o zmianie ustawy o zawodach pielęgniarki i położnej (uznawanie kwalifikacji)",
    "stypendia dla studentów zagranicznych",
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
    # Everyone's registers and benefits: a bill changing them for foreigners names them (measured
    # on the 1500 processes of term 10, these titles hit 4–14 unrelated bills each).
    "Rządowy projekt ustawy o zmianie ustawy o świadczeniach opieki zdrowotnej finansowanych ze środków publicznych",
    "Poselski projekt ustawy o zmianie ustawy - Kodeks wyborczy",
    "Rządowy projekt ustawy o zmianie ustawy o aplikacji mObywatel oraz niektórych innych ustaw",
    "Rządowy projekt ustawy o zmianie ustawy - Prawo o szkolnictwie wyższym i nauce",
    "ewidencja ludności i numer PESEL",
    "wysokość świadczenia wychowawczego",
]

# Weak hits: worth counting inside a text next to a strong pattern, not a verdict on their own.
WEAK_TITLES = [
    "Rządowy projekt ustawy o zmianie ustawy o Straży Granicznej",
    "opodatkowanie dochodów nierezydentów i certyfikat rezydencji podatkowej",
    "kontrola przekraczania granicy w ruchu towarowym",
]


@pytest.mark.parametrize("title", POSITIVE)
def test_positive_titles_are_candidates(title: str) -> None:
    assert accept_title_hits(prefilter.match(title)), title


@pytest.mark.parametrize("title", WEAK_TITLES)
def test_weak_titles_are_read_but_do_not_decide(title: str) -> None:
    hits = prefilter.match(title)
    assert hits and not accept_title_hits(hits), title


def test_the_bare_genitive_of_wiza_is_matched_but_not_wizja() -> None:
    assert prefilter.match("wydawanie wiz") == ["wizy"]
    assert not prefilter.match("wizja i telewizja")


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


def test_tolerated_stay_is_matched() -> None:
    assert "pobyt_kwalifikowany" in KeywordPrefilter().match("zgoda na pobyt tolerowany")


def test_match_counts_and_acceptance_threshold() -> None:
    text = "cudzoziemiec ... cudzoziemcy ... zezwolenie na pobyt czasowy ... wiza"
    counts = KeywordPrefilter().match_counts(text)
    assert counts["cudzoziemcy"] == 2 and counts["zezwolenie_pobyt"] == 1 and counts["wizy"] == 1
    assert accept_text_hits(counts, min_distinct=2, min_occurrences=3)
    assert not accept_text_hits({"cudzoziemcy": 1}, min_distinct=2, min_occurrences=3)
    assert accept_text_hits({"cudzoziemcy": 3}, min_distinct=2, min_occurrences=3)
    assert not accept_text_hits({}, min_distinct=1, min_occurrences=1)


def test_weak_patterns_never_decide_alone() -> None:
    # druk 2695 (food quality) named Straż Graniczna four times as an inspecting authority
    assert not accept_text_hits({"straz_graniczna": 4}, min_distinct=2, min_occurrences=3)
    assert not accept_text_hits(
        {"straz_graniczna": 2, "schengen": 1}, min_distinct=2, min_occurrences=3
    )
    assert accept_text_hits(
        {"straz_graniczna": 2, "cudzoziemcy": 1}, min_distinct=2, min_occurrences=3
    )
    # druk 2839 (excise) said "legalizacja" 140 times about excise stamps; crypto-asset bills
    # combine it with Straż Graniczna. A stay is legalised for a "cudzoziemiec", named as well.
    assert not accept_text_hits({"legalizacja": 140}, min_distinct=2, min_occurrences=3)
    assert not accept_text_hits(
        {"legalizacja": 3, "straz_graniczna": 2}, min_distinct=2, min_occurrences=3
    )
    assert accept_text_hits({"legalizacja": 2, "cudzoziemcy": 1}, min_distinct=2, min_occurrences=3)


def test_spans_point_at_the_hits() -> None:
    text = "Wniosek składa cudzoziemiec; Straż Graniczna kontroluje."
    spans = KeywordPrefilter().spans(text)
    assert [text[a:b] for a, b in spans] == ["cudzoziemiec", "Straż Graniczna"]


def test_real_print_text_passes_the_text_prefilter(print_3039_pdf_text: str) -> None:
    counts = KeywordPrefilter().match_counts(print_3039_pdf_text)
    assert counts["cudzoziemcy"] > 3
    assert accept_text_hits(counts, min_distinct=2, min_occurrences=3)
