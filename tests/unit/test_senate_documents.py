from pathlib import Path

import pytest

from lexinform.senate_documents import inspect_senate_resolution, senate_subject_key

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
