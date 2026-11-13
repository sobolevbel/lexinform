"""Project admission distinguishes declared act types from words inside a statute's title."""

import datetime as dt

import pytest

from lexinform.errors import UnsupportedRclProjectError
from lexinform.models import Bill, BillStatus, RclProject, process_summary
from lexinform.models.rcl import unsupported_project_kind
from lexinform.services.sources import RclTextSource
from tests.conftest import RCL_FIXTURES
from tests.harness import rcl_project


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("projekt rozporządzenia Ministra Spraw Zagranicznych", "rozporządzenie"),
        ("  PROJEKT\nROZPORZĄDZENIA Rady Ministrów", "rozporządzenie"),
        ("Rozporządzenie Ministra Spraw Zagranicznych", "rozporządzenie"),
        ("Projekt uchwały Rady Ministrów", "uchwała"),
        ("UCHWAŁA RADY MINISTRÓW", "uchwała"),
        ("Projekt zarządzenia Prezesa Rady Ministrów", "zarządzenie"),
        ("Zarządzenie ministra", "zarządzenie"),
        ("Projekt ustawy o wykonywaniu rozporządzenia Unii Europejskiej", None),
        ("Ustawa o zmianie ustawy – Karta Nauczyciela", None),
        ("Projekt autopoprawki do rządowego projektu ustawy", None),
        ("Projekty ustawy o zmianie ustawy – Kodeks pracy", None),
        ("Projekt o zmianie ustawy o spółdzielniach mieszkaniowych", None),
        ("Program modernizacji Policji, Straży Granicznej i PSP w latach 2026-2029", None),
        ("Projekt z dnia 22 sierpnia 2025 r.", None),
        ("", None),
    ],
)
def test_only_explicit_non_statute_openings_are_excluded(title: str, expected: str | None) -> None:
    assert unsupported_project_kind(title) == expected


def test_saved_consular_fee_project_cannot_be_labelled_as_a_statute() -> None:
    project = RclProject.model_validate_json((RCL_FIXTURES / "project_12415804.json").read_bytes())

    with pytest.raises(UnsupportedRclProjectError) as caught:
        process_summary(project, term=10)

    assert (caught.value.number, caught.value.kind) == ("RCL/12415804", "rozporządzenie")


def test_source_rejects_a_legacy_non_bill_even_without_documents() -> None:
    project = rcl_project(title="Projekt rozporządzenia w sprawie opłat konsularnych", stages=())
    now = dt.datetime(2026, 11, 13, tzinfo=dt.UTC)
    bill = Bill(
        summary=process_summary(rcl_project(), term=10),
        rcl=project,
        status=BillStatus.ANALYSIS_PENDING,
        first_seen_at=now,
        last_checked_at=now,
    )

    with pytest.raises(UnsupportedRclProjectError, match="rozporządzenie"):
        RclTextSource().locate(bill)
