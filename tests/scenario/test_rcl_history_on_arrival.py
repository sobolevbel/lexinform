"""RCL projects the listing shows as modified today whose timeline stopped years ago (B64, B65)."""

import datetime as dt

import pytest

from lexinform.models import BillStatus, RclProject
from tests.harness import (
    RCL,
    World,
    rcl_document,
    rcl_folder,
    rcl_project,
    rcl_stage,
)

OLD_TEXT = (
    rcl_document(794885, "projekt_ustawy.DOCX", created=dt.date(2018, 9, 10)),
    rcl_document(794886, "uzasadnienie.doc", created=dt.date(2018, 9, 10)),
)


def _touched_today(w: World, project: RclProject) -> None:
    """RCL re-dates the listing row alone, as it did for a block of Minister Finansów projects."""
    w.add_rcl_project(project)
    today = w.clock.now().date()
    w.rcl.listing = [
        row.model_copy(update={"modified": today}) if row.id == project.id else row
        for row in w.rcl.listing
    ]


@pytest.mark.xfail(strict=True, reason="B64: an abandoned project reads as live on first sight")
def test_a_project_abandoned_years_ago_gets_no_card_when_rcl_touches_it() -> None:
    w = World()
    _touched_today(
        w,
        rcl_project(
            created=dt.date(2018, 9, 3),
            modified=dt.date(2018, 10, 9),
            term_label="VIII",
            consultation=None,
            stages=(
                rcl_stage(
                    2,
                    "Uzgodnienia",
                    "active",
                    rcl_folder(13223896, "Projekt", *OLD_TEXT),
                    modified=dt.date(2018, 10, 9),
                ),
                rcl_stage(3, "Konsultacje publiczne", "not_started", modified=None),
                rcl_stage(14, "Skierowanie projektu ustawy do Sejmu", "not_started", modified=None),
            ),
        ),
    )

    report = w.run()

    assert (report.rcl_discovered, report.over_on_arrival) == (1, 1)
    assert (report.analyzed, report.published) == (0, 0)
    assert w.bill(RCL).status is BillStatus.SKIPPED_CLOSED


@pytest.mark.xfail(strict=True, reason="B65: a project sent to a past Sejm waits for a druk")
def test_a_project_sent_to_a_past_sejm_gets_no_card_when_rcl_touches_it() -> None:
    w = World()
    _touched_today(
        w,
        rcl_project(
            created=dt.date(2020, 12, 29),
            modified=dt.date(2021, 1, 7),
            term_label="IX",
            consultation=None,
            stages=(
                rcl_stage(
                    12,
                    "Rada Ministrów",
                    "reached",
                    rcl_folder(13223896, "Projekt", *OLD_TEXT),
                    modified=dt.date(2021, 1, 5),
                ),
                rcl_stage(
                    14,
                    "Skierowanie projektu ustawy do Sejmu",
                    "reached",
                    modified=dt.date(2021, 1, 7),
                ),
            ),
        ),
    )

    report = w.run()

    assert (report.rcl_discovered, report.over_on_arrival) == (1, 1)
    assert (report.analyzed, report.published) == (0, 0)
    assert w.bill(RCL).status is BillStatus.SKIPPED_CLOSED
