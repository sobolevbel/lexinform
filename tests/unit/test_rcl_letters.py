"""The consultation letter parser on two real letters and a few phrasings."""

from datetime import date

from lexinform.rcl_letters import LetterInfo, deadline_of, parse_letter
from tests.conftest import RCL_FIXTURES


def _letter(name: str) -> str:
    return (RCL_FIXTURES / name).read_text(encoding="utf-8")


def test_electronically_signed_letter_gives_days_and_address_but_no_date() -> None:
    info = parse_letter(_letter("pismo_uc164.txt"))

    assert info == LetterInfo(
        letter_date=None, days=7, deadline=None, email="dep.prawny@mswia.gov.pl"
    )
    assert deadline_of(info, published=date(2026, 9, 1)) == date(2026, 9, 8)


def test_dated_letter_counts_from_its_own_date_and_ignores_the_letterhead_address() -> None:
    info = parse_letter(_letter("pismo_uc133.txt"))

    assert (info.letter_date, info.days, info.email) == (
        date(2026, 8, 28),
        14,  # the first term is for everyone; the 30 days are for social partners
        "sekretariatDTD@mi.gov.pl",
    )
    assert deadline_of(info, published=date(2026, 8, 29)) == date(2026, 9, 11)


def test_absolute_deadline_wins_over_the_relative_one() -> None:
    text = (
        "Warszawa, dnia 2.09.2026 r. Uwagi proszę zgłaszać w terminie do dnia 30 września 2026 r. "
        "na adres e-mail: konsultacje@mz.gov.pl."
    )

    info = parse_letter(text)

    assert info == LetterInfo(
        letter_date=date(2026, 9, 2),
        days=None,
        deadline=date(2026, 9, 30),
        email="konsultacje@mz.gov.pl",
    )
    assert deadline_of(info, published=date(2026, 9, 3)) == date(2026, 9, 30)


def test_a_letter_that_says_nothing_useful_yields_no_deadline() -> None:
    info = parse_letter("Szanowni Państwo, w załączeniu przekazuję projekt ustawy.")

    assert info == LetterInfo()
    assert deadline_of(info, published=date(2026, 9, 3)) is None


def test_an_address_counts_when_something_tells_the_reader_to_send_comments_there() -> None:
    info = parse_letter("Uwagi w ciągu 21 dni prosimy kierować do: uwagi@kprm.gov.pl")

    assert (info.days, info.email) == (21, "uwagi@kprm.gov.pl")


def test_a_date_out_of_a_consultation_range_is_not_the_consultation_deadline() -> None:
    """Letters quote the bill's own dates — "ustawa obowiązuje do dnia 31 grudnia 2030 r." — and
    that is not a day anyone may still send an opinion by."""
    text = (
        "Warszawa, dnia 1 września 2026 r. Uprzejmie proszę o zajęcie stanowiska w terminie 14 "
        "dni od dnia otrzymania niniejszego pisma na adres: dep@mswia.gov.pl. Projekt przewiduje, "
        "że przepis obowiązuje do dnia 31 grudnia 2030 r."
    )

    info = parse_letter(text)

    assert (info.days, info.deadline) == (14, date(2030, 12, 31))
    assert deadline_of(info, published=date(2026, 9, 1)) == date(2026, 9, 15)


def test_a_letterhead_is_not_an_address_for_comments() -> None:
    """A ministry's letterhead labels its own switchboard "adres e-mail:", one word from the
    instruction "na adres". Over the corpus that label was handed to readers as the address for
    comments in 17 letters, and the blind fallback to the first e-mail in the letter did it in
    426 more — always the switchboard, because the extracted text opens with the letterhead."""
    letterhead = (
        "Ministerstwo Cyfryzacji\n"
        "telefon: 22 245 59 15 adres: ul. Królewska 27 adres e-mail: sekretariat.DP@cyfra.gov.pl\n"
        "Szanowni Państwo,\n"
        "uprzejmie proszę o przedstawienie stanowiska w terminie 21 dni.\n"
    )

    info = parse_letter(letterhead)

    assert info.email is None
    assert info.days == 21


def test_the_instruction_is_what_gives_the_address() -> None:
    letter = (
        "Ministerstwo Zdrowia\n"
        "telefon: +48 22 250 01 46 adres email: kancelaria@mz.gov.pl\n"
        "Uprzejmie proszę o przekazanie uwag w wersji edytowalnej "
        "na adres: dep.prawny@mswia.gov.pl w terminie 14 dni.\n"
    )

    info = parse_letter(letter)

    assert info.email == "dep.prawny@mswia.gov.pl"
    assert info.days == 14
