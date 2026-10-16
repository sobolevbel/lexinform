import datetime as dt
from pathlib import Path

import pytest

from lexinform.models import AmendmentSourceEvidence, SenateResolutionIdentity
from lexinform.senate_documents import (
    inspect_senate_resolution,
    resolution_date_matches,
    senate_subject_key,
    validate_senate_scan,
)

FIXTURES = Path(__file__).parents[1] / "fixtures/sejm"


@pytest.fixture
def resolution() -> str:
    return (FIXTURES / "senate_3213_resolution.txt").read_text()


def test_real_resolution_preserves_every_amendment_and_justification(resolution: str) -> None:
    evidence = inspect_senate_resolution(resolution)

    assert evidence.complete and evidence.outcome == "amendments"
    assert str(evidence.adopted_on) == "2026-10-13"
    assert evidence.subject == "o asystencji osobistej osób z niepełnosprawnościami"
    assert evidence.text == resolution and evidence.justification
    assert "w art. 118" in evidence.operative_text


@pytest.mark.parametrize(
    "name",
    [
        "senate_8_31_pdf.txt",
        "senate_8_1841_pdf.txt",
        "senate_9_1637_docx.txt",
        "senate_9_1641_docx.txt",
        "senate_10_2140_docx.txt",
    ],
)
def test_real_letter_spacing_budget_tables_and_preamble_are_read(name: str) -> None:
    text = (FIXTURES / name).read_text()

    evidence = inspect_senate_resolution(text)

    assert evidence.complete and evidence.outcome == "amendments"
    assert evidence.operative_text.strip() and evidence.text in text


def test_an_official_word_without_its_end_remains_unconfirmed() -> None:
    text = (FIXTURES / "senate_9_1211_docx.txt").read_text()

    evidence = inspect_senate_resolution(text)

    assert evidence.limitation == "missing_resolution_end"


def test_an_official_misfiled_word_identifies_the_law_in_its_contents() -> None:
    text = (FIXTURES / "senate_9_655_docx.txt").read_text()

    evidence = inspect_senate_resolution(text)

    assert evidence.complete and evidence.subject == "o Funduszu Medycznym"


@pytest.mark.parametrize(
    "kind",
    [
        "cover",
        "justification",
        "initiative",
        "quoted",
        "missing_end",
        "missing_body",
        "blank_middle_page",
        "two_resolutions",
        "negative_decision",
        "rejected",
    ],
)
def test_incomplete_and_other_documents_do_not_become_amendments(
    resolution: str, kind: str
) -> None:
    evidence = inspect_senate_resolution(resolution)
    texts = {
        "cover": (FIXTURES / "senate_3213_cover.txt").read_text(),
        "justification": "UZASADNIENIE\n" + resolution[resolution.index("Uzasadnienie") :],
        "initiative": resolution.replace(
            "w sprawie ustawy", "w sprawie wniesienia projektu ustawy", 1
        ),
        "quoted": "SPRAWOZDANIE\nCytat z uchwały:\n" + resolution,
        "missing_end": resolution[: resolution.index("MARSZAŁEK SENATU")],
        "missing_body": resolution.replace(evidence.operative_text, "\n"),
        "blank_middle_page": resolution.replace(
            evidence.operative_text,
            evidence.operative_text[:100] + "\f– 3 –\f" + evidence.operative_text[100:],
        ),
        "two_resolutions": resolution + "\n" + resolution,
        "negative_decision": resolution.replace(
            "wprowadza do jej tekstu", "nie wprowadza do jej tekstu"
        ),
        "rejected": resolution[: resolution.index("wprowadza do jej tekstu")]
        + "odrzuca tę ustawę.\nMARSZAŁEK SENATU\nNazwisko\nUZASADNIENIE\n"
        + resolution,
    }

    inspected = inspect_senate_resolution(texts[kind])

    assert not inspected.complete or inspected.outcome != "amendments"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("o opłacie 1,25 zł", "o opłacie 1,52 zł"),
        ("o art. 12", "o art. 1 2"),
        ("o świadczeniach", "o świadczeniach niepieniężnych"),
        ("o świadczeniach", "o świadczeniach oraz cudzoziemcach"),
        ("o zmianie ustawy 2026", "o zmianie ustawy 2027"),
        ("o art. 15 ust. 2", "o art. 15 ust. 3"),
    ],
)
def test_subject_identity_preserves_numbers_negations_scope_and_references(
    before: str, after: str
) -> None:
    assert senate_subject_key(before) != senate_subject_key(after)


def test_subject_identity_only_tolerates_layout_and_punctuation() -> None:
    assert senate_subject_key("o Fun duszu Medycznym.") == senate_subject_key(
        "o Funduszu Medycznym"
    )
    assert senate_subject_key("o niepełno-\nsprawności") == senate_subject_key(
        "o niepełnosprawności"
    )


@pytest.mark.parametrize(
    "name",
    [
        "senate_8_1840_docx.txt",
        "senate_9_2219_docx.txt",
        "senate_10_887_docx.txt",
        "senate_10_2068_docx.txt",
    ],
)
def test_exact_third_title_in_justification_corroborates_a_retyped_title(name: str) -> None:
    text = (FIXTURES / name).read_text()

    evidence = inspect_senate_resolution(text)

    assert evidence.complete and evidence.corroborated_subject
    assert evidence.heading_subject != evidence.recited_subject
    assert evidence.text == text


def test_an_unrelated_mention_in_justification_is_not_a_title_witness(resolution: str) -> None:
    changed = resolution.replace("w sprawie ustawy", "w sprawie ustawy nie", 1)
    changed = changed[: changed.index("Uzasadnienie")]
    changed += "\nUZASADNIENIE\nSenat omawiał też ustawę nie o asystencji osobistej.\n"

    assert not inspect_senate_resolution(changed).complete


def test_a_closed_single_removal_at_word_body_end_is_complete() -> None:
    text = (FIXTURES / "senate_9_1211_docx.txt").read_text()

    evidence = inspect_senate_resolution(text, word_eof=True)

    assert evidence.complete and evidence.word_eof and evidence.text == text
    assert not inspect_senate_resolution(text).complete


@pytest.mark.parametrize("mutation", ["unfinished", "extra", "negative", "long"])
def test_word_body_end_does_not_prove_an_arbitrary_amendment_is_complete(mutation: str) -> None:
    text = (FIXTURES / "senate_9_1211_docx.txt").read_text()
    texts = {
        "unfinished": text.rstrip().removesuffix("."),
        "extra": text + "\nw art. 2 dodaje się",
        "negative": text.replace("skreśla się", "nie skreśla się"),
        "long": text.replace("skreśla się", "dodaje się"),
    }

    assert not inspect_senate_resolution(texts[mutation], word_eof=True).complete


def test_date_interval_is_bounded_by_adoption_and_print_not_a_fixed_tolerance() -> None:
    stage, passed, printed = dt.date(2023, 7, 13), dt.date(2023, 6, 16), dt.date(2023, 7, 19)
    assert resolution_date_matches(
        dt.date(2023, 7, 14), stage=stage, document=printed, passed=passed
    )
    for actual in (dt.date(2023, 6, 15), dt.date(2023, 7, 20)):
        assert not resolution_date_matches(actual, stage=stage, document=printed, passed=passed)
    assert not resolution_date_matches(
        dt.date(2023, 7, 14), stage=stage, document=printed, passed=None
    )


def scan_proof() -> tuple[AmendmentSourceEvidence, SenateResolutionIdentity]:
    evidence = AmendmentSourceEvidence(
        subject="o cudzoziemcach",
        adopted_on=dt.date(2026, 10, 13),
        decision="amendments",
        complete=True,
        resolution_page=2,
        amendment_pages=[2, 3],
        amendment_quote="w art. 1 skreśla się pkt 2.",
    )
    source = SenateResolutionIdentity(
        sha256="raw",
        subject=evidence.subject,
        adopted_on=dt.date(2026, 10, 13),
        scan=True,
        pages=4,
        expected_subjects=(evidence.subject,),
    )
    return evidence, source


def test_complete_scan_evidence_is_checked_against_frozen_metadata() -> None:
    evidence, source = scan_proof()
    validate_senate_scan(evidence, source)


@pytest.mark.parametrize(
    "change",
    [
        {"complete": False},
        {"decision": "acceptance"},
        {"subject": "o niecudzoziemcach"},
        {"adopted_on": None},
        {"adopted_on": dt.date(2026, 10, 14)},
        {"resolution_page": None},
        {"resolution_page": 3},
        {"amendment_pages": []},
        {"amendment_pages": [2, 4]},
        {"amendment_pages": [2, 3, 4, 5]},
        {"amendment_pages": [2, 2, 3]},
        {"amendment_quote": "Senat wniósł poprawki do ustawy."},
    ],
)
def test_incomplete_or_conflicting_scan_evidence_is_refused(change: dict[str, object]) -> None:
    evidence, source = scan_proof()
    with pytest.raises(ValueError):
        validate_senate_scan(evidence.model_copy(update=change), source)


def test_missing_scan_evidence_is_refused() -> None:
    _, source = scan_proof()
    with pytest.raises(ValueError):
        validate_senate_scan(None, source)
