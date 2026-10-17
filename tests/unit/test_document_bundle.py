import hashlib

import pytest

from lexinform.document_selection import bill_selection, deduplicate_bundle, selection_for_input
from lexinform.models import DocumentPart, DocumentSelection
from lexinform.models.documents import DocumentRole
from tests.scenario.test_document_selection_contract import law


def part(text: str, role: DocumentRole = "bill") -> DocumentPart:
    return DocumentPart(
        url=f"https://official.test/{role}",
        role=role,
        text=text,
        text_end=len(text),
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def choose(*parts: DocumentPart) -> DocumentSelection:
    return bill_selection(DocumentSelection(parts=parts), source_basis=("rcl/1/stage/1",))


def test_own_annex_requires_a_matching_source_part() -> None:
    body = law().replace("Art. 2.", "Art. 2. Wzór określa załącznik nr 2 do niniejszej ustawy.")
    annex = "Załącznik nr 2 do projektu ustawy\nWYKAZ\n200 zł"

    missing = choose(part(body))
    selected = choose(part(body), part(annex, "normative_annex"))

    assert missing.state == "waiting" and "missing_own_annex" in missing.limitations
    assert selected.state == "selected"
    assert selected.links[0].status == "confirmed"
    assert len(selected.links[0].evidence) == 2
    assert (
        selected.normative_sha256
        != choose(part(body), part(annex.replace("200", "300"), "normative_annex")).normative_sha256
    )


def test_a_reference_to_an_existing_act_does_not_require_its_annex() -> None:
    text = law().replace(
        "Art. 2.", "Art. 2. W załączniku do ustawy z dnia 1 maja 2020 r. pkt 1 uchyla się."
    )

    assert choose(part(text)).state == "selected"


def test_an_implementing_annex_cannot_confirm_the_statutory_bundle() -> None:
    result = choose(
        part(law()), part("Załącznik do rozporządzenia ministra\nWZÓR", "normative_annex")
    )

    assert result.state == "ambiguous" and "foreign_annex" in result.limitations
    assert result.links[0].status == "conflicting"


def test_annex_name_without_content_link_does_not_prove_completeness() -> None:
    result = choose(part(law()), part("WYKAZ\n200 zł", "normative_annex"))

    assert result.state == "unverified" and "annex_link_unknown" in result.limitations


@pytest.mark.parametrize("amount,expected", [("200", "confirmed"), ("300", "conflicting")])
def test_amount_comparison_requires_the_same_explicit_project_norm(
    amount: str, expected: str
) -> None:
    body = law().replace("Cudzoziemiec składa wniosek w terminie 30 dni.", "Opłata wynosi 200 zł.")
    support = f"UZASADNIENIE\nW art. 1 projektu ustawy opłata wynosi {amount} zł."

    result = choose(part(body), part(support, "justification"))

    assert result.links[0].status == expected
    assert result.state == ("ambiguous" if expected == "conflicting" else "selected")
    assert len(result.links[0].evidence) == 2


@pytest.mark.parametrize(
    "support",
    [
        "Koszty systemu wynoszą 300 zł.",
        "W art. 1 projektu ustawy świadczenie wynosi 300 zł.",
        "W art. 2 projektu ustawy opłata wynosi 300 zł.",
        "W art. 1 ustawy z dnia 1 maja 2020 r. opłata wynosi 300 zł.",
        "W art. 1 projektu ustawy opłata wynosi 300 USD.",
        "W art. 1 projektu ustawy opłata wynosi nie mniej niż 300 zł.",
        "W art. 1 projektu ustawy opłaty wynoszą 200 zł oraz 300 zł.",
    ],
)
def test_unrelated_numbers_and_ambiguous_quantities_are_not_conflicts(support: str) -> None:
    body = law().replace("Cudzoziemiec składa wniosek w terminie 30 dni.", "Opłata wynosi 200 zł.")

    result = choose(part(body), part("UZASADNIENIE\n" + support, "justification"))

    assert result.state == "selected"
    assert result.links[0].status == "unknown"
    assert "support_applicability_unknown" in result.limitations


def test_unchanged_support_can_remain_applicable_to_a_newer_draft() -> None:
    body = law(day=2).replace(
        "Cudzoziemiec składa wniosek w terminie 30 dni.", "Opłata wynosi 200 zł."
    )
    support = "Projekt z dnia 1 września 2026 r.\nUZASADNIENIE\nW art. 1 projektu ustawy opłata wynosi 200 zł."

    result = choose(part(body), part(support, "justification"))

    assert result.state == "selected" and result.links[0].status == "confirmed"
    assert "support_applicability_unknown" not in result.limitations


def test_prepared_input_cannot_claim_a_dropped_normative_part() -> None:
    annex = "Załącznik do projektu ustawy\nWYKAZ\n200 zł"
    selected = choose(part(law()), part(annex, "normative_annex"))

    missing = selection_for_input(selected, law())
    full = selection_for_input(selected, law() + "\n" + annex)

    assert missing.state == "unverified"
    assert "normative_text_omitted" in missing.limitations
    assert any(span.losses for span in missing.coverage if span.stage == "analysis")
    assert full.state == "selected"
    assert (
        DocumentSelection.model_validate_json(full.without_text().model_dump_json())
        == full.without_text()
    )


def test_repeated_physical_parts_are_not_reanalysed_twice() -> None:
    from_text = part(law())
    from_alias = from_text.model_copy(update={"url": "https://official.test/alias"})

    text, parts = deduplicate_bundle(law() + "\f" + law(), (from_text, from_alias))

    assert text == law() and parts == (from_text,)


def test_conflicting_own_revision_dates_remain_a_typed_conflict() -> None:
    text = "Projekt z dnia 2 września 2026 r.\n" + law()

    result = choose(part(text))

    assert result.state == "ambiguous"
    assert "draft_version_conflict" in result.limitations
    assert sum(fact.kind == "version_conflict" for fact in result.facts) == 2


def test_an_existing_act_annex_number_is_not_a_required_project_annex() -> None:
    text = law().replace(
        "Art. 2.",
        "Art. 2. Załącznik nr 15 do ustawy otrzymuje brzmienie określone w załączniku nr 1 do niniejszej ustawy.",
    )
    annex = "Załącznik nr 1 do projektu ustawy\nWYKAZ"

    result = choose(part(text), part(annex, "normative_annex"))

    assert "missing_own_annex" not in result.limitations
    assert result.state != "waiting"


def test_an_available_annex_with_unclear_link_is_not_reported_as_missing() -> None:
    text = law().replace("Art. 2.", "Art. 2. Wykaz określa załącznik do ustawy.")
    annex = "Załącznik do ustawy z dnia …\nWYKAZ"

    result = choose(part(text), part(annex, "normative_annex"))

    assert result.state == "unverified"
    assert "annex_link_unknown" in result.limitations
    assert "missing_own_annex" not in result.limitations


def test_an_inline_own_annex_has_a_link_and_is_not_missing() -> None:
    text = law().replace("Art. 2.", "Art. 2. Wzór określa załącznik do niniejszej ustawy.")
    text += "\nZałącznik do projektu ustawy\nWYKAZ\n200 zł"

    result = choose(part(text))

    assert result.state == "selected"
    assert any(
        link.source_part == link.target_part and link.status == "confirmed" for link in result.links
    )


def test_a_required_continuation_part_is_in_the_digest_and_input_coverage() -> None:
    continuation = part("Art. 4. Dodatkowy termin wynosi 30 dni.", "normative_continuation")
    selected = choose(part(law()), continuation)
    changed = choose(
        part(law()), part(continuation.text.replace("30", "60"), "normative_continuation")
    )

    assert selected.normative_sha256 != changed.normative_sha256
    assert "normative_text_omitted" in selection_for_input(selected, law()).limitations


def test_a_required_volume_can_contain_the_own_annex() -> None:
    text = law().replace("Art. 2.", "Art. 2. Wzór określa załącznik do niniejszej ustawy.")
    continuation = "Art. 4. Przepis końcowy.\nZałącznik do projektu ustawy\nWYKAZ\n200 zł"

    result = choose(part(text), part(continuation, "normative_continuation"))

    assert result.state == "selected"
    assert any(
        link.source_part == 1 and link.target_part == 0 and link.status == "confirmed"
        for link in result.links
    )


def test_required_volumes_cannot_silently_mix_two_dated_drafts() -> None:
    result = choose(part(law()), part(law(day=2), "normative_continuation"))

    assert result.state == "ambiguous"
    assert "draft_version_conflict" in result.limitations


def test_a_plain_annex_reference_in_an_amended_article_does_not_prove_missing_own_annex() -> None:
    text = law().replace(
        "Art. 2.", "Art. 2. Art. 5 otrzymuje brzmienie: „Wzór określa załącznik nr 3 do ustawy.”;"
    )

    result = choose(part(text))

    assert result.state == "unverified"
    assert "missing_own_annex" not in result.limitations


def test_an_inline_annex_can_begin_after_a_pdf_page_break() -> None:
    text = law().replace("Art. 2.", "Art. 2. Wzór określa załącznik do niniejszej ustawy.")
    text += "\fZałącznik do ustawy\nWYKAZ\n200 zł"

    assert choose(part(text)).state == "selected"


def test_a_bare_numbered_annex_heading_is_available_but_its_link_is_unproven() -> None:
    text = law().replace("Art. 2.", "Art. 2. Wzór określa załącznik nr 2 do niniejszej ustawy.")
    text += "\fZałączniki do ustawy z dnia ……\nZałącznik nr 2\nWYKAZ\n200 zł"

    result = choose(part(text))

    assert result.state == "unverified"
    assert "annex_link_unknown" in result.limitations
    assert "missing_own_annex" not in result.limitations


def test_justification_inside_the_same_file_does_not_declare_a_required_own_annex() -> None:
    text = law() + "\fUZASADNIENIE\nW załączniku do projektu ustawy przedstawimy przykład."

    result = choose(part(text))

    assert "missing_own_annex" not in result.limitations
