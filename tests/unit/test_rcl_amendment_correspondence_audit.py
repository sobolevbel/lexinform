import hashlib

import pytest
from tools.audit_rcl_amendment_archives import correspondence_quotes

LETTER = "Szanowny Panie Sekretarzu, w odpowiedzi na uwagi przedstawiam zmiany.\n"


def test_correspondence_quote_keeps_exact_nested_text() -> None:
    body = "Art. 6. Wyraz „termin” zachowuje dotychczasowe znaczenie."
    text = LETTER + f"Proponuję nadać art. 6 projektu następujące brzmienie:\n„{body}”."
    (row,) = correspondence_quotes(text)
    assert row["status"] == "quoted"
    assert row["reference"] == "art. 6"
    assert text[row["start"] : row["end"]] == body
    assert row["sha256"] == hashlib.sha256(body.encode()).hexdigest()


@pytest.mark.parametrize(
    ("phrase", "status"),
    [
        ("Art. 4 otrzymuje brzmienie: „Nie domknięto cytatu.", "unresolved_boundary"),
        ("Art. 4 w związku z art. 5 otrzymuje brzmienie: „Zmiana.”", "unresolved_reference"),
    ],
)
def test_correspondence_quote_records_ambiguous_evidence(phrase: str, status: str) -> None:
    (row,) = correspondence_quotes(LETTER + phrase)
    assert row["status"] == status
    assert "end" not in row and "sha256" not in row


def test_corpus_quote_does_not_scan_a_bill_as_correspondence() -> None:
    assert correspondence_quotes("USTAWA\nArt. 4 otrzymuje brzmienie: „Zmiana.”") == []


def test_unclosed_quote_cannot_capture_the_next_article() -> None:
    text = LETTER + (
        "Art. 4 otrzymuje brzmienie: „Brak zamknięcia.\n"
        "Art. 5 otrzymuje brzmienie: „Termin wynosi 30 dni.”.”"
    )
    first, second = correspondence_quotes(text)
    assert first["status"] == "unresolved_boundary"
    assert text[second["start"] : second["end"]] == "Termin wynosi 30 dni."
