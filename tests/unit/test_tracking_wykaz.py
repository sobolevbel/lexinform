"""Following a plan in the register: what is told, and what is only stored."""

from lexinform.models import PublicationKind
from tests.harness import WYKAZ, World, wykaz_entry


def _followed() -> World:
    w = World()
    w.add_wykaz_entry()
    w.run()
    return w


def test_the_government_dropping_a_project_is_told_under_the_card() -> None:
    w = _followed()

    w.add_wykaz_entry(
        entry=wykaz_entry(
            status="Wycofany",
            resignation="Wykreślenie projektu podyktowane jest nową koncepcją.",
        )
    )
    report = w.run()

    assert report.updates == 1
    bill, change, reply_to = w.publisher.updates[0]
    assert (bill.number, reply_to) == (WYKAZ, w.card_id(WYKAZ))
    assert change.closure_detected and change.passed is False
    stored = w.bill(WYKAZ)
    assert stored.wykaz is not None and stored.wykaz.is_withdrawn
    assert stored.summary.closure_date is not None


def test_a_project_dropped_without_a_status_but_with_a_reason_is_told_too() -> None:
    w = _followed()

    w.add_wykaz_entry(entry=wykaz_entry(status="Niezrealizowany"))
    w.run()

    assert w.publisher.updates
    assert w.publication(WYKAZ, PublicationKind.STATUS_UPDATE) is not None


def test_the_same_withdrawal_is_not_told_twice() -> None:
    w = _followed()
    w.add_wykaz_entry(entry=wykaz_entry(status="Wycofany"))
    w.run()

    report = w.run()

    assert report.updates == 0
    assert len(w.publisher.updates) == 1


def test_a_slipped_quarter_is_stored_and_not_told() -> None:
    w = _followed()

    w.add_wykaz_entry(entry=wykaz_entry(planned_adoption="IV kwartał 2026 r."))
    report = w.run()

    assert (report.updates, w.publisher.updates) == (0, [])
    stored = w.bill(WYKAZ)
    assert stored.wykaz is not None and stored.wykaz.planned_quarter == (2026, 4)


def test_an_entry_that_left_the_register_is_left_as_it_was() -> None:
    w = _followed()
    w.wykaz.entries_by_number.clear()

    report = w.run()

    assert (report.updates, report.errors) == (0, [])
    assert w.bill(WYKAZ).wykaz is not None
