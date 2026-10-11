import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import Digest, DigestEntry, ProcessDetail, PublicationKind, Upcoming
from lexinform.publication_titles import publication_title
from tests.formatting import ACT, NOW, assert_telegram_html, bill_of


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Poselski projekt ustawy o zmianie ustawy o cudzoziemcach", "O cudzoziemcach"),
        ("Rządowy projekt ustawy o cudzoziemcach", "O cudzoziemcach"),
        ("Senacki projekt ustawy o zmianie ustawy - Kodeks pracy", "Kodeks pracy"),
        ("Komisyjny projekt ustawy o zmianie ustawy – Prawo oświatowe", "Prawo oświatowe"),
        ("Obywatelski projekt ustawy o zmianie ustawy Karta Nauczyciela", "Karta Nauczyciela"),
        (
            "Przedstawiony przez Prezydenta Rzeczypospolitej Polskiej projekt ustawy o obywatelstwie polskim",
            "O obywatelstwie polskim",
        ),
        ("Projekt Ustawy o zmianie ustawy- Kodeks pracy", "Kodeks pracy"),
        ("Ustawa o zmianie ustawy o zmianie ustawy o cudzoziemcach", "O cudzoziemcach"),
        (
            "Projekt ustawy zmieniającej ustawę o zmianie ustawy - Prawo pocztowe",
            "Prawo pocztowe",
        ),
        ("Rządowy projekt ustawy zmieniający ustawę o zmianie ustawy o odpadach", "O odpadach"),
        ("zmieniającą ustawę o zmianie ustawy o odpadach", "O odpadach"),
        ("zmieniająca ustawę o zmianie ustawy o odpadach", "O odpadach"),
        (
            "Ustawa z dnia 12 kwietnia 2024 r. zmieniająca ustawę o zmianie ustawy o odpadach",
            "O odpadach",
        ),
        (
            "Poselski projekt ustawy o zmianie ustawy z dnia 15 grudnia 2016 r. o zmianie ustawy o grach hazardowych oraz niektórych innych ustaw",
            "O grach hazardowych oraz niektórych innych ustaw",
        ),
        (
            "Projekt ustawy o zmianie ustawy z dnia 27 czerwca 2003 roku o rencie socjalnej",
            "O rencie socjalnej",
        ),
        ("Projekt ustawy o zmianie ustawy ‒ Prawo probiercze", "Prawo probiercze"),
        (
            "Projekt ustawy - Przepisy wprowadzające ustawę o cudzoziemcach",
            "Przepisy wprowadzające ustawę o cudzoziemcach",
        ),
        (
            "\nPROJEKT\tUSTAWY o zmianie\u00a0ustawy o cudzoziemcach\n oraz innych ustaw",
            "O cudzoziemcach oraz innych ustaw",
        ),
        (
            "Projekt ustawy o zmianie ustawy o cudzoziemcach oraz ustawy o zmianie ustawy o ochronie zdrowia",
            "O cudzoziemcach oraz ustawy o zmianie ustawy o ochronie zdrowia",
        ),
        (
            "Projekt ustawy o zmianie niektórych ustaw w związku z przeciwdziałaniem COVID-19",
            "O zmianie niektórych ustaw w związku z przeciwdziałaniem COVID-19",
        ),
        ("Projekt ustawy o uchyleniu ustawy o podatku", "O uchyleniu ustawy o podatku"),
        ("Projekt ustawy o ratyfikacji Umowy z Japonią", "O ratyfikacji Umowy z Japonią"),
    ],
)
def test_shortens_only_the_leading_statute_wrappers(title: str, expected: str) -> None:
    assert publication_title(title) == expected
    assert publication_title(expected) == expected


@pytest.mark.parametrize(
    "title",
    [
        "",
        "Ustawa",
        "Ustawa o zmianie ustawy",
        "Ustawa z dnia 17 lipca 2026 r. o zmianie ustawy",
        "Projekt ustawy o zmianie ustawy budżetowej na rok 2024",
        "Projekt ustawy budżetowej na rok 2027",
        "Projekt ustawy o zmianie ustawy pomocy społecznej",
        "Projekt ustawy o zmianie ustawy zmianie ustawy – Prawo farmaceutyczne",
        "Autopoprawka do projektu ustawy o zmianie ustawy o cudzoziemcach",
        "Projekt rozporządzenia zmieniającego rozporządzenie w sprawie zezwoleń",
        "Poselski projekt uchwały w sprawie zmiany Regulaminu Sejmu",
        "  Nieznany projekt o zmianie ustawy o cudzoziemcach\n",
        "Ustawa o zmianie ustawy oraz innych ustaw",
        "O cudzoziemcach",
    ],
)
def test_keeps_unrecognized_incomplete_and_non_statute_titles(title: str) -> None:
    assert publication_title(title) == title


@pytest.mark.parametrize("language", ["ru", "en"])
def test_card_and_act_titles_are_shortened_and_escaped_without_changing_the_bill(
    process_3039: ProcessDetail, language: str
) -> None:
    title = 'Projekt ustawy o zmianie ustawy o cudzoziemcach <test> & "cytat"'
    bill = bill_of(
        process_3039.model_copy(update={"title": title}),
        act=ACT.model_copy(update={"title": f"Ustawa z dnia 17 lipca 2026 r. {title[15:]}"}),
    )
    original = bill.model_dump_json()
    formatter = MessageFormatter(language)

    messages = (
        formatter.new_bill(bill, None),
        formatter.act_published(bill),
        formatter.in_force(bill),
    )

    for message in messages:
        assert_telegram_html(message.text)
        assert '<b>O cudzoziemcach &lt;test&gt; &amp; "cytat"</b>' in message.text
        assert "o zmianie ustawy" not in message.text
    assert bill.model_dump_json() == original


def test_digest_shortens_titles_before_clipping_cards_and_consultations() -> None:
    title = "Projekt ustawy " + "o zmianie ustawy " * 8 + "o cudzoziemcach"
    digest = Digest(
        ref="2026-W37",
        since=dt.date(2026, 9, 7),
        until=dt.date(2026, 9, 13),
        cards=(
            DigestEntry(
                term=10, number="3039", title=title, kind=PublicationKind.NEW_BILL, sent_at=NOW
            ),
        ),
        consultations=(
            Upcoming(term=10, number="3039", title=title, deadline=dt.date(2026, 9, 30)),
        ),
    )

    text = MessageFormatter("ru", today=lambda: dt.date(2026, 9, 13)).digest(digest).text

    assert_telegram_html(text)
    assert text.count("O cudzoziemcach") == 2
    assert "o zmianie ustawy" not in text
    assert digest.cards[0].title == title
    assert digest.consultations[0].title == title
