from lexinform.authors import MpDirectory, parse_cover_letter
from lexinform.models import Mp

COMMITTEE_LETTER = """Druk nr 1962
Na podstawie art. 32 ust. 2 regulaminu Sejmu Komisja Sprawiedliwości i Praw
Człowieka wnosi projekt ustawy:
- o zmianie ustawy - Prawo o adwokaturze.

Do reprezentowania stanowiska Komisji w pracach nad projektem ustawy został
upoważniony poseł Paweł Śliz.

 Przewodniczący Komisji

(-) Paweł Śliz

                Tłoczono z polecenia  Marszałka Sejmu Rzeczypospolitej Polskiej

Projekt
U S T AWA
"""

MPS = (
    Mp(
        id=1,
        first_name="Daria",
        last_name="Gosek-Popiołek",
        accusative_name="Darię Gosek-Popiołek",
        club="Lewica",
    ),
    Mp(
        id=2,
        first_name="Anna",
        second_name="Maria",
        last_name="Żukowska",
        accusative_name="Annę Marię Żukowską",
        club="Lewica",
    ),
    Mp(
        id=3,
        first_name="Włodzimierz",
        last_name="Czarzasty",
        accusative_name="Włodzimierza Czarzastego",
        club="Lewica",
    ),
    Mp(
        id=4, first_name="Paweł", last_name="Śliz", accusative_name="Pawła Śliza", club="Polska2050"
    ),
    Mp(id=5, first_name="Jan", last_name="Kowalski", club="KO"),
)


def test_cover_letter_of_a_deputies_bill_lists_signatories(print_3039_pdf_text: str) -> None:
    letter = parse_cover_letter(print_3039_pdf_text)
    assert letter.representative == "Darię Gosek-Popiołek"
    assert len(letter.signatories) == 21
    assert "Daria Gosek-Popiołek" in letter.signatories  # hyphen broken across lines in the PDF
    assert "Anita Kucharska-Dziedzic" in letter.signatories
    assert (
        letter.signatories[0] == "Bożena Borowiec"
        and letter.signatories[-1] == "Anna Maria Żukowska"
    )


def test_cover_letter_of_a_committee_bill_has_a_representative_only() -> None:
    letter = parse_cover_letter(COMMITTEE_LETTER)
    assert letter.representative == "Paweł Śliz"
    assert letter.signatories == ("Paweł Śliz",)  # the chair's signature


def test_directory_resolves_clubs_middle_names_and_accusative_forms() -> None:
    directory = MpDirectory.from_mps(MPS)
    letter = parse_cover_letter(
        "niżej podpisani posłowie wnoszą projekt ustawy:\n- o zmianie.\n"
        "Do reprezentowania wnioskodawców upoważniamy posłankę Darię Gosek-Popiołek.\n\n"
        " (-)  Anna Maria Żukowska;  (-)  Włodzimierz Czarzasty; (-) Daria Gosek -\nPopiołek;"
        "  (-)  Jan Kowalski;  (-)  Ktoś Nieznany.\n\nTłoczono z polecenia Marszałka"
    )
    authors = directory.resolve(letter)
    assert authors.representative == "Daria Gosek-Popiołek"
    assert authors.representative_club == "Lewica"
    assert authors.clubs == (("Lewica", 3), ("KO", 1))
    assert authors.signatories == 5 and authors.unresolved == 1
