"""The act's fingerprint: equal for the same law in another layout, different for another law."""

from lexinform.law_digest import law_digest

RCL_TEXT = """Projekt z dnia 24 kwietnia 2026 r.

USTAWA
z dnia …………… 2026 r.
o zmianie ustawy o cudzoziemcach

Art. 1. W ustawie z dnia 12 grudnia 2013 r. o cudzoziemcach (Dz. U. z 2025 r. poz. 1079 i 1088)
wprowadza się następujące zmiany:
art. 114 ust. 1 otrzymuje brzmienie:
,,1. Zezwolenia na pobyt czasowy udziela się cudzoziemcowi na okres nie dłuższy niż 3 lata.”;
art. 115 − uchyla się.
Art. 2. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.
\fUZASADNIENIE
Projekt skraca okres pobytu, bo tak postanowiono w uzgodnieniach.
"""

DRUK_TEXT = """Druk nr 3141
\fProjekt
U S T A W A
z dnia
o zmianie ustawy o cudzoziemcach1)

Art. 1. W ustawie z dnia 12 grudnia 2013 r. o cudzoziemcach (Dz. U. z 2025 r. poz. 1079, 1088
i 1402) wprowadza się następujące zmiany:
1) art. 114 ust. 1 otrzymuje brzmienie:
„1. Zezwolenia na pobyt czasowy udziela się cudzoziemcowi na okres nie dłuż-
szy niż 3 lata.”;
2) art. 115 – uchyla się.

1) Niniejszą ustawą zmienia się ustawę z dnia 12 grudnia 2013 r. o cudzoziemcach.

– 2 –
Art. 2. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.
\fUZASADNIENIE
Zupełnie inne uzasadnienie, napisane od nowa przed skierowaniem do Sejmu.
"""


def test_the_same_act_in_another_layout_has_the_same_fingerprint() -> None:
    assert law_digest(RCL_TEXT) is not None
    assert law_digest(RCL_TEXT) == law_digest(DRUK_TEXT)


def test_a_changed_number_in_the_act_changes_the_fingerprint() -> None:
    assert law_digest(DRUK_TEXT.replace("3 lata", "2 lata")) != law_digest(RCL_TEXT)


def test_a_changed_word_in_the_act_changes_the_fingerprint() -> None:
    assert law_digest(DRUK_TEXT.replace("uchyla się", "skreśla się")) != law_digest(RCL_TEXT)


def test_the_uzasadnienie_is_not_part_of_the_act() -> None:
    rewritten = RCL_TEXT.replace("uzgodnieniach", "konsultacjach publicznych")
    assert law_digest(rewritten) == law_digest(RCL_TEXT)


def test_a_text_without_articles_has_no_fingerprint() -> None:
    letter = "Szanowna Pani Marszałek,\nprzesyłam projekt ustawy wraz z uzasadnieniem.\n" * 20
    assert law_digest(letter) is None
    assert law_digest("") is None
