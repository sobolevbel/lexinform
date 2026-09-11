"""Pure helpers of the wykaz models: numbering, the planned quarter, the fingerprint."""

import datetime as dt

from lexinform.models import (
    ApplicantType,
    WykazEntry,
    has_process,
    is_wykaz_number,
    wykaz_entry_number,
    wykaz_fingerprint,
    wykaz_number,
    wykaz_summary,
)

PUBLISHED = dt.datetime(2026, 5, 12, 15, 21, tzinfo=dt.UTC)


def _entry(**overrides: object) -> WykazEntry:
    fields: dict[str, object] = {
        "number": "UD408",
        "title": "Projekt ustawy o zmianie ustawy o cudzoziemcach",
        "kind": "Projekty ustaw",
        "goals": "Polska przekształciła się z państwa emigracyjnego w państwo imigracyjne.",
        "essence": "Projekt zakłada milczące zakończenie postępowania.",
        "organ": "MSWiA",
        "planned_adoption": "III kwartał 2026 r.",
        "published_at": PUBLISHED,
        "web_url": "https://www.gov.pl/web/premier/projekt-ustawy-o-cudzoziemcach",
    }
    fields.update(overrides)
    return WykazEntry.model_validate(fields)


def test_a_register_entry_has_no_sejm_process() -> None:
    entry = _entry()

    assert entry.bill_number == "WPL/UD408"
    assert wykaz_number("UD408") == "WPL/UD408"
    assert wykaz_entry_number("WPL/UD408") == "UD408"
    assert is_wykaz_number("WPL/UD408")
    assert not has_process("WPL/UD408")


def test_the_description_is_the_goals_and_the_essence() -> None:
    entry = _entry()

    description = entry.description

    assert description is not None
    assert description.startswith("Polska przekształciła")
    assert "milczące zakończenie" in description


def test_an_entry_without_goals_and_essence_has_no_description() -> None:
    assert _entry(goals="", essence="").description is None


def test_the_summary_of_an_open_entry_is_a_government_bill_that_is_not_closed() -> None:
    summary = wykaz_summary(_entry(), term=10)

    assert (summary.number, summary.term) == ("WPL/UD408", 10)
    assert summary.applicant is ApplicantType.GOVERNMENT
    assert summary.change_date == PUBLISHED
    assert summary.document_date == dt.date(2026, 5, 12)
    assert (summary.closure_date, summary.passed) == (None, None)
    assert not summary.eu_related
    assert not summary.has_process


def test_a_withdrawn_entry_is_closed_and_not_passed() -> None:
    entry = _entry(status="Wycofany")

    summary = wykaz_summary(entry, term=10)

    assert entry.is_withdrawn and not entry.is_open
    assert (summary.closure_date, summary.passed) == (dt.date(2026, 5, 12), False)


def test_a_resignation_note_alone_withdraws_the_entry() -> None:
    assert _entry(resignation="Odstąpiono od prac nad projektem.").is_withdrawn


def test_a_uc_number_marks_the_bill_as_eu_related() -> None:
    assert wykaz_summary(_entry(number="UC164"), term=10).eu_related


def test_the_planned_quarter_is_read_and_the_rest_of_the_field_is_not() -> None:
    assert _entry().planned_quarter == (2026, 3)
    assert _entry(planned_adoption="II/III kwartał 2026 r.").planned_quarter == (2026, 3)
    assert _entry(planned_adoption="IV kwartał 2027 r. ").planned_quarter == (2027, 4)
    # The field carries the adoption note as often as not; only the quarter is taken from it.
    realised = "II kwartał 2025 r. - ZREALIZOWANY Rada Ministrów przyjęła 6 maja 2025 r."
    assert _entry(planned_adoption=realised).planned_quarter == (2025, 2)
    assert _entry(planned_adoption="niezwłocznie").planned_quarter is None
    assert _entry(planned_adoption="").planned_quarter is None


def test_the_fingerprint_ignores_the_publication_date_and_the_entrys_url() -> None:
    entry = _entry()

    same = _entry(
        published_at=dt.datetime(2026, 9, 8, 13, 30, tzinfo=dt.UTC),
        web_url="https://www.gov.pl/web/premier/projekt-ustawy-o-cudzoziemcach13",
    )

    assert wykaz_fingerprint(same) == wykaz_fingerprint(entry)


def test_the_fingerprint_follows_the_plan_itself() -> None:
    entry = _entry()

    assert wykaz_fingerprint(_entry(essence="Inna istota.")) != wykaz_fingerprint(entry)
    assert wykaz_fingerprint(_entry(status="Wycofany")) != wykaz_fingerprint(entry)
    assert wykaz_fingerprint(_entry(planned_adoption="IV kwartał 2026 r.")) != wykaz_fingerprint(
        entry
    )
    assert wykaz_fingerprint(_entry(rcl_project_id=12412103)) != wykaz_fingerprint(entry)
