"""Production technical equivalence keeps values, their order and removal boundaries."""

import pytest

from lexinform.law_diff import diff_laws
from lexinform.law_digest import law_digest
from lexinform.services.analysis import text_digest
from tests.unit.test_law_diff import ACT


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("92,67 zł", "9267 zł"),
        ("\n1000\n", "\n1001\n"),
        ("-92,67", "92,67"),
        ("20 26", "2026"),
        ("20-\n26", "2026"),
        ("≤ 20", "≥ 20"),
        ("5ac", "5ad"),
        ("nie może", "może"),
        ("cudzoziemców", "obywateli polskich"),
    ],
)
def test_every_comparison_layer_keeps_material_tokens(old: str, new: str) -> None:
    before = ACT + "\n" + old + "\n"
    after = ACT + "\n" + new + "\n"
    assert text_digest(before) != text_digest(after)
    assert law_digest(before) != law_digest(after)
    diff = diff_laws(before, after)
    assert diff is not None and diff.hunks


def test_a_table_value_at_a_page_boundary_is_preserved() -> None:
    before = ACT + "\f1000\nOpłata za wydanie dokumentu."
    after = before.replace("1000", "1001")
    assert law_digest(before) != law_digest(after)
    diff = diff_laws(before, after)
    assert diff is not None and diff.hunks


def test_a_footnote_cannot_swallow_the_following_provision() -> None:
    before = ACT + "\n1) Niniejszą ustawą zmienia się ustawę o obywatelstwie.\n\f"
    before += "Cudzoziemiec nie może złożyć wniosku po upływie 30 dni."
    for after in (before.replace("nie może", "może"), before.replace("30 dni", "31 dni")):
        assert law_digest(before) != law_digest(after)
        diff = diff_laws(before, after)
        assert diff is not None and diff.hunks


def test_a_citation_cannot_hide_prose_in_its_parentheses() -> None:
    before = ACT + "Przepisy (Dz. U. z 2026 r. poz. 7; stosuje się przez 30 dni)."
    after = before.replace("30 dni", "31 dni")
    assert law_digest(before) != law_digest(after)
    diff = diff_laws(before, after)
    assert diff is not None and diff.hunks


def test_an_empty_appendix_date_and_citation_are_technical() -> None:
    before = ACT + "Załączniki do ustawy z dnia ……… r. (Dz. U. poz.\n………)\nZałącznik nr 1"
    after = ACT + "Załączniki do ustawy z dnia\nZałącznik nr 1"
    assert law_digest(before) == law_digest(after)


def test_table_proof_keeps_the_rest_of_the_act_and_both_row_values() -> None:
    row = "gminy\tadministracja\t0,24\tiloczyn liczby mieszkańców\t1\n"
    extracted = "gminy administracja 0,24 iloczyn\n1\nliczby mieszkańców\n"
    diff = diff_laws(ACT + row, ACT + extracted)
    assert diff is not None and not diff.hunks
    proof = next(item for item in diff.evidence if item.rule == "table_row")
    assert "0,24" in proof.old and "0,24" in proof.new
    for changed in (extracted.replace("\n1\n", "\n2\n"), extracted.replace("0,24", "0,25")):
        diff = diff_laws(ACT + row, ACT + changed)
        assert diff is not None and diff.hunks
    diff = diff_laws(ACT + row, ACT.replace("2034", "2033") + extracted)
    assert diff is not None and diff.hunks


def test_technical_equivalence_has_bounded_evidence() -> None:
    stamp = (
        "Opracowano pod względem prawnym, legislacyjnym i redakcyjnym\n"
        "Dyrektor Departamentu Legislacyjnego\nMarcin Gubała\n/podpisano elektronicznie/\n"
    )
    diff = diff_laws(ACT, ACT + stamp)
    assert diff is not None and not diff.hunks
    proof = next(item for item in diff.evidence if item.rule == "editorial_signature")
    assert proof.old.strip() == stamp.strip() and proof.new == ""
    diff = diff_laws(ACT, ACT + stamp + "Cudzoziemiec może podjąć pracę.")
    assert diff is not None and diff.hunks


def test_an_incomplete_act_cannot_be_proved_equal_by_diff() -> None:
    assert diff_laws("Art. 1. Krótki fragment.", "Art. 1. Krótki fragment.") is None


def test_context_budget_counts_the_full_provisions_and_definitions() -> None:
    definition = "Art. 4. Ilekroć mowa o cudzoziemcu, rozumie się także członka jego rodziny."
    diff = diff_laws(ACT + definition, ACT.replace("2034", "2033") + definition)
    assert diff is not None and definition in diff.old_law and definition in diff.new_law
    assert diff.chars > len(diff.old_law) + len(diff.new_law)


def test_only_explicit_reference_ranges_ignore_spacing() -> None:
    before = ACT + "Przepis ust. 3-5 stosuje się do cudzoziemców."
    assert law_digest(before) == law_digest(before.replace("3-5", "3 - 5"))
    assert law_digest(before) != law_digest(before.replace("3-5", "3 - 6"))
    assert law_digest(ACT + "Kwota 3 -5.") != law_digest(ACT + "Kwota 3-5.")


def test_certification_requires_a_name_role_and_no_following_provision() -> None:
    stamp = (
        "ZA ZGODNOŚĆ POD WZGLĘDEM PRAWNYM, LEGISLACYJNYM I REDAKCYJNYM\n"
        "Renata Łućko\nZastępca Dyrektora\n"
        "Departamentu Prawnego w Ministerstwie Finansów\n"
        "/- podpisano kwalifikowanym podpisem elektronicznym/\n"
    )
    assert law_digest(ACT) == law_digest(ACT + stamp)
    assert law_digest(ACT) != law_digest(ACT + stamp + "Cudzoziemiec nie może podjąć pracy.")
    assert law_digest(ACT) != law_digest(ACT + stamp.replace("Renata Łućko", ""))
