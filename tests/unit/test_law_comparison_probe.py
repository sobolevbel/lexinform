"""Experimental proofs must reject meaningful changes even when the old fingerprint agrees."""

import pytest
from tools.probe_law_comparison import align_table, articles, guarded_signature, technical

ACT = """Art. 1. Cudzoziemiec nie może złożyć wniosku po upływie 14 dni.
Art. 2. Wysokość świadczenia wynosi 92,67 zł. Przepisu art. 5ac nie stosuje się do dzieci.
Art. 3. Ustawa wchodzi w życie z dniem 1 stycznia 2027 r.
"""


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("14 dni", "15 dni"),
        ("nie może", "może"),
        ("92,67", "9267"),
        ("5ac", "5ad"),
        ("do dzieci", "do wszystkich dzieci"),
        ("2027", "2028"),
        ("92,67", "-92,67"),
    ],
)
def test_guard_keeps_material_facts(old: str, new: str) -> None:
    assert guarded_signature(ACT) != guarded_signature(ACT.replace(old, new))


def test_a_number_on_its_own_line_is_not_assumed_to_be_a_page_number() -> None:
    original = ACT + "Opłata\n1000\nZa wydanie dokumentu.\n"
    assert guarded_signature(original) != guarded_signature(original.replace("1000", "1001"))


def test_a_split_year_is_joined_only_in_an_explicit_year_context() -> None:
    year = ACT + "Kwota przysługuje na rok 2026.\n"
    assert guarded_signature(year) == guarded_signature(year.replace("rok 2026", "rok 202 6"))
    assert guarded_signature(year) != guarded_signature(year.replace("rok 2026", "rok 202 7"))
    numbers = ACT + "W załączniku ujęto przypadki 20 26.\n"
    assert guarded_signature(numbers) != guarded_signature(numbers.replace("20 26", "2026"))


def test_a_bibliographic_footnote_ends_before_the_next_provision() -> None:
    note = "1) Zmiany tekstu jednolitego wymienionej ustawy zostały ogłoszone w Dz. U. z 2025 r. poz. 7.\n"
    before = ACT.replace("Art. 2.", note + "Art. 2.")
    assert "Zmiany tekstu" not in technical(before)
    assert "Art. 2." in technical(before)
    assert guarded_signature(before) != guarded_signature(before.replace("92,67", "9267"))


@pytest.mark.parametrize("separator", [" ", "\n"])
def test_a_signed_editorial_stamp_is_technical_but_following_prose_is_not(separator: str) -> None:
    stamp = (
        f"Opracowano pod względem prawnym,{separator}legislacyjnym i redakcyjnym\n"
        "Dyrektor Departamentu Legislacyjnego\nMarcin Gubała\n/podpisano elektronicznie/\n"
    )
    assert technical(ACT + stamp).strip() == ACT.strip()
    additional = "Cudzoziemiec może podjąć pracę.\n"
    assert additional in technical(ACT + stamp + additional)


def test_a_real_date_is_not_an_empty_appendix_date() -> None:
    before = ACT + "Załączniki do ustawy z dnia 1 stycznia 2027 r. (Dz. U. poz. 7)\n"
    assert guarded_signature(before) != guarded_signature(before.replace("2027 r. (", "2028 r. ("))


def test_table_alignment_keeps_cells_with_their_row() -> None:
    old = "gminy\tadministracja\t0,24\tiloczyn liczby mieszkańców\t1\n"
    new = "gminy administracja 0,24 iloczyn\n1\nliczby mieszkańców\n"
    aligned, matched, total = align_table(old, new)
    assert (matched, total) == (1, 1)
    assert aligned.strip() == old.strip()
    for changed in (
        new.replace("\n1\n", "\n2\n"),
        new.replace("0,24", "0,25"),
        new.replace("0,24", "024"),
        new.replace("iloczyn", "nie iloczyn"),
    ):
        assert align_table(old, changed)[1] == 0


def test_swapping_coefficients_between_table_rows_is_a_change() -> None:
    old = "gminy\toświata\t0,24\tliczba dzieci\t1\npowiaty\toświata\t0,25\tliczba szkół\t2\n"
    swapped = "gminy oświata 0,24 liczba dzieci 2\npowiaty oświata 0,25 liczba szkół 1\n"
    assert align_table(old, swapped)[1] == 0


def test_article_matching_cannot_certify_a_fragment_or_ambiguous_numbering() -> None:
    assert articles("Art. 2. Zmienia się wysokość opłaty.") is None
    assert articles(ACT + "Art. 2. Zmienia się wysokość opłaty.") is None
