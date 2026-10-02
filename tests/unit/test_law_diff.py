"""The act's changes between two texts: the extraction's noise out, every change of wording in."""

import pytest

from lexinform.law_diff import diff_laws

ACT = """USTAWA
o zmianie ustawy o cudzoziemcach

Art. 1. W ustawie z dnia 12 grudnia 2013 r. o cudzoziemcach (Dz. U. z 2025 r. poz. 1079)
wprowadza się następujące zmiany:
a) art. 114 ust. 2 otrzymuje brzmienie:
„2. Zezwolenia na pobyt czasowy udziela się cudzoziemcowi na okres nie dłuższy niż 3 lata.”;
Art. 2. Przepisy stosuje się do dnia 31 grudnia 2034 r.
Art. 3. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.
"""


def test_the_same_act_in_another_layout_has_no_changes() -> None:
    other = (
        ACT.replace("pobyt czasowy", "po byt czasowy")
        .replace("a) art. 114", "art. 114")
        .replace("(Dz. U. z 2025 r. poz. 1079)", "(Dz. U. z 2025 r. poz. 1079 i 1088)")
        .replace("następujące", "następu-\njące")
    )

    diff = diff_laws(ACT, other)

    assert diff is not None and diff.hunks == ()


def test_a_changed_year_is_a_change() -> None:
    diff = diff_laws(ACT, ACT.replace("2034", "2033"))

    assert diff is not None
    (hunk,) = diff.hunks
    assert (hunk.old, hunk.new) == ("2034", "2033")
    assert hunk.before.endswith("do dnia 31 grudnia") and hunk.after.startswith("r. Art. 3.")


def test_a_replaced_single_digit_is_a_change() -> None:
    diff = diff_laws(ACT, ACT.replace("ust. 2 otrzymuje", "ust. 3 otrzymuje"))

    assert diff is not None and [(h.old, h.new) for h in diff.hunks] == [("2", "3")]


@pytest.mark.parametrize("added", ["do", "18", "i", "w"])
@pytest.mark.parametrize("removed", [False, True])
def test_short_words_and_numbers_are_not_extraction_noise(added: str, removed: bool) -> None:
    changed = ACT.replace("na okres", f"na {added} okres")
    before, after = (changed, ACT) if removed else (ACT, changed)

    diff = diff_laws(before, after)

    assert diff is not None and len(diff.hunks) == 1
    assert (diff.hunks[0].old, diff.hunks[0].new) == ((added, "") if removed else ("", added))


def test_an_added_provision_is_quoted_whole_and_counted() -> None:
    added = "Art. 2a. Cudzoziemiec może złożyć wniosek elektronicznie.\n"
    diff = diff_laws(ACT, ACT.replace("Art. 3.", added + "Art. 3."))

    assert diff is not None
    (hunk,) = diff.hunks
    assert hunk.old == "" and "Cudzoziemiec może złożyć wniosek elektronicznie." in hunk.new
    assert diff.changed_words == 7 and 0 < diff.ratio < 0.15


def test_a_text_without_an_act_cannot_be_compared() -> None:
    assert diff_laws("UZASADNIENIE\nProjekt skraca okres pobytu.", ACT) is None
